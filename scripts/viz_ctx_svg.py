#!/usr/bin/env python3
"""
viz_ctx_svg.py — gráficos SVG inline de viz-ctx.py (epic
add-context-dashboard, ctx-viz)

Purpose: SVG estáticos stdlib-only con tooltips nativos (<title>) para
la página de contexto: histograma del pico de contexto y barras stacked
(100%) de composición por fuente.

Responsibilities:
- _hist_svg(hist, band): histograma con eje, ticks y banda de
  percentiles p50/p75/p90/p95/p99 etiquetada.
- _stacked_svg(shares, legend): barra stacked por fuente con la paleta
  SEG_COLORS compartida con la leyenda HTML.

Rationale: sin CDN ni librerías externas (anti-goal FPA-145). Split de
viz-ctx.py (coffe-v5f): módulo aparte para que el archivo principal
quede bajo el límite de líneas.
"""

import html

from viz_ctx_stats import PCT_KEYS, _fmt_k  # noqa: F401  (_hist_svg usa ambos)

SEG_COLORS = ("c1", "c2", "c3", "c4", "c5", "c6")


def _stacked_svg(shares, legend, w=360, h=60):
    """Barra stacked (100%) con tooltips nativos; leyenda aparte."""
    parts = [f'<svg viewBox="0 0 {w} {h}" class="stack" role="img" '
             f'aria-label="share por fuente">']
    x = 0.0
    order = [s for s in legend if s in shares]
    for i, src in enumerate(order):
        sw = shares[src] * w
        cls = SEG_COLORS[i % len(SEG_COLORS)]
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
