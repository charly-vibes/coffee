#!/usr/bin/env python3
"""
viz_ctx_stats.py — estadística y formato stdlib-only de viz-ctx.py
(epic add-context-dashboard, ctx-viz)

Purpose: helpers puros compartidos por las vistas del dashboard de
contexto: percentiles interpolados, OLS simple, días de mes (FPA-041) y
formateo de tok/chars (k/M).

Responsibilities:
- funciones puras sin estado ni I/O, usadas por viz_ctx_model.py,
  viz_ctx_svg.py, viz_ctx_docs.py y viz_ctx_views.py.

Rationale: split de viz-ctx.py (coffe-v5f) — capa de matemática aparte
para que los módulos de modelo/vista queden bajo el límite de líneas.
"""

PCT_KEYS = ("p50", "p75", "p90", "p95", "p99")
N_BINS = 10


def _month_days(ym):
    """Días del mes YYYY-MM (FPA-041)."""
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


def _fmt_human(n):
    """16_000_000 → '16.0M', 500_000 → '500.0k', 999 → '999'."""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(int(n))


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
