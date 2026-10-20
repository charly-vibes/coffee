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
        "time": _build_time(cm.get("monthly_model") or [],
                            (report.get("metadata") or {})
                            .get("date_range", {}).get("end")),
        "monthly_model": cm.get("monthly_model") or [],
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
</style>
</head>
<body>
<h1>Contexto de sesiones (Pi) · Distribución del pico</h1>
<p class="scope">{scope}</p>
<div class="card">
  <h2>Pico de contexto por sesión — {n_clean} sesiones limpias{extra}</h2>
  {hist_html}
  <p class="muted">Estimación: chars / chars_per_token
     (<span class="tag">{tag}</span> = {cpt})
     — {bias} · ratio de validación vs usage real: {ratio} (mediana).</p>
</div>
{comp_html}
{time_html}
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
