"""Módulo extraído de viz-fpa.py (decomposición, comportamiento idéntico)."""
import calendar, dataclasses, html, json, re  # noqa: F401
import sys
from datetime import date, datetime  # noqa: F401
from html.parser import HTMLParser  # noqa: F401
from pathlib import Path  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fpa_config  # noqa: F401,E402
import schema_validate  # noqa: F401,E402
import site_theme  # noqa: F401,E402




# Vistas pre-calculadas por periodo (FPA-026): Python computa, JS re-escala

def _year_windows(months, y, out):
    """Ventanas anuales y YTD del año `y` (+ sus trimestres)."""
    wy = [ym for ym in months if ym.startswith(y)]
    pwy = [ym for ym in months if ym.startswith(str(int(y) - 1))]
    prior = pwy if len(pwy) == len(wy) else None
    out.append((f"ytd:{y}", f"YTD {y}", wy, prior)); out.append((f"year:{y}", y, wy, prior))
    for q in range(1, 5):
        _quarter_window(months, y, q, out)

def _quarter_window(months, y, q, out):
    qms = [f"{y}-{m:02d}" for m in range(3 * q - 2, 3 * q + 1) if f"{y}-{m:02d}" in months]
    if not qms:
        return
    pq = (q - 1) or 4
    py = f"{int(y) - 1}" if q == 1 else y
    pms = [f"{py}-{m:02d}" for m in range(3 * pq - 2, 3 * pq + 1) if f"{py}-{m:02d}" in months]
    out.append((f"q:{y}Q{q}", f"Q{q} {y[2:]}", qms,
                pms if len(pms) == len(qms) else None))

def _custom_ranges(months, known, out):
    # FPA-090: rangos custom — todo rango contiguo de ≥2 meses que no sea ya un
    # preset; prior = rango inmediato anterior de la misma longitud.
    for i in range(len(months)):
        for j in range(i + 2, len(months) + 1):
            window = months[i:j]; key = f"range:{window[0]}..{window[-1]}"
            if key in known:
                continue
            plen = j - i
            prior = months[i - plen:i] if i - plen >= 0 else None
            label = f"{month_label(window[0])} → {month_label(window[-1])}"
            out.append((key, label, window, prior))

def _windows(months):
    """Ventanas predefinidas (FPA-090): periodo completo, YTD por año, cada
    año, cada trimestre y cada mes con datos, más todo rango contiguo
    custom de ≥2 meses — todas pre-calculadas; el selector JS solo elige
    entre vistas ya calculadas."""
    out = [("all", "Todo el periodo", list(months), None)]
    for y in sorted({ym[:4] for ym in months}):
        _year_windows(months, y, out)
    out += [(f"month:{ym}", month_label(ym), [ym],
             [months[months.index(ym) - 1]] if months.index(ym) else None)
            for ym in months]
    _custom_ranges(months, {k for k, *_ in out}, out)
    return out

@dataclasses.dataclass(frozen=True)
class _TreeCtx:
    """Contexto compartido de una pasada de árbol (ventana + prior)."""
    window: list
    prior: object
    total_cost: float
    is_time: bool = False
    full: bool = False

def _tree_base_row(node, ctx):
    """Celdas base de una fila de árbol (FPA-023/024); JS no formatea nada."""
    cost = _cost_of(node, ctx.window)
    inter = _inter_of(node, ctx.window)
    prior_cost = None if node.get("totals_only") else (
        _cost_of(node, ctx.prior) if ctx.prior else None)
    return {
        "key": node["key"], "label": node["label"], "cost": round(cost, 2),
        "cost_display": fmt_usd(cost) if cost else "n/a",
        "pct_display": (f"{100 * cost / ctx.total_cost:.1f}%"
                        if ctx.total_cost and cost else "n/a"),
        "interactions": inter, "interactions_display": fmt_int(inter),
        "per_1k_display": (fmt_usd(cost / (inter / 1000)) if inter and cost else "n/a"),  # FPA-042
        "delta_display": (_delta_display(_pct_change(cost, prior_cost))
                          if prior_cost is not None else "n/a"),
        "totals_only": bool(node.get("totals_only")), "children": [],
    }

def _tree_budget_fields(row, node, ctx):
    """Columnas de presupuesto/varianza (solo árbol de tiempo, FPA-023)."""
    if not ctx.is_time:
        return
    # coffe-9p4: los presupuestos solo existen en meses in-budget; si la ventana
    # mezcla meses fuera del periodo presupuestado (root, años), la varianza
    # compara solo el tramo in-budget en vez de mezclar 10 meses de coste contra 6 de presupuesto.
    bmonths = [m for m in ctx.window if m in node["by_month"]
               and node["by_month"][m].get("budget")]
    budget = sum(node["by_month"][m]["budget"] for m in bmonths)
    if budget:
        variance = (sum(node["by_month"][m]["cost"] for m in bmonths)
                    if len(bmonths) < len(ctx.window)
                    else _cost_of(node, ctx.window)) - budget
    row["budget_display"] = fmt_usd(budget) + " *" if budget else "n/a"
    row["variance_display"] = (f"{'+' if variance >= 0 else '-'}"
                               f"${abs(variance):,.2f}" if budget else "n/a")
    row["variance_pct_display"] = (f"{100 * variance / budget:+.1f}%"
                                   if budget else "n/a")

def _tree_rows(node, window=None, prior=None, total_cost=None):
    """Filas de árbol pre-formateadas para una ventana (columnas FPA-023/024).
    Firma compatible con la original (node, window, prior, total_cost,
    is_time=False, full=False); los tests la llaman de forma posicional."""
    return _tree_rows_ctx(node, _TreeCtx(window, prior, total_cost))

def _tree_rows_ctx(node, ctx):
    """Implementación sobre _TreeCtx; cada celda sale lista para render,
    JS no formatea nada."""
    row = _tree_base_row(node, ctx)
    _tree_budget_fields(row, node, ctx)
    if node.get("url"):  # coffe-vp8: passthrough del link al repo
        row["url"] = node["url"]
        row["private"] = bool(node.get("private"))
    row["children"] = [_tree_rows_ctx(c, dataclasses.replace(ctx))
                       for c in node["children"]
                       if ctx.full or not c.get("totals_only")]  # drill Model: solo periodo completo
    return row

def _delta_display(delta):
    return "n/a" if delta is None else f"{'+' if delta >= 0 else ''}{delta * 100:.1f}%"

def _checked_trees(report, cfg):
    """Árboles con roll-up verificado (FPA-101; falla ruidosa si se rompe)."""
    trees = {"time": build_time_tree(report, cfg), "tool": build_tool_tree(report),
             "portfolio": build_portfolio_tree(report, cfg)}
    for name, tree in trees.items():
        bad = check_rollup(tree)
        if bad:
            raise ValueError(f"roll-up inconsistente en árbol {name}: {bad}")
    return trees

def _view_trees(trees, window, prior, full):
    """Las tres columnas de árboles para una ventana."""
    return {
        "time": _tree_rows_ctx(trees["time"], _TreeCtx(
            window, prior, _cost_of(trees["time"], window), is_time=True)),
        "tool": _tree_rows_ctx(trees["tool"], _TreeCtx(
            window, prior, _cost_of(trees["tool"], window))),
        "portfolio": _tree_rows_ctx(trees["portfolio"], _TreeCtx(
            window, prior, _cost_of(trees["portfolio"], window), full=full)),
    }

def build_views(report, cfg):
    """FPA-026: una vista pre-calculada por ventana (todo, año, trimestre, mes).
    Cada vista trae KPIs con delta vs ventana previa, las tres columnas de
    árboles y su limitación si aplica (FPA-027)."""
    months_all = [m["ym"] for m in build_months(report)]
    ing = kpi_ingredients(report, cfg)
    months = [m for m in months_all if m in ing]
    trees = _checked_trees(report, cfg)
    views = {}
    for key, label, window, prior in _windows(months):
        full = key == "all"
        view = {"label": label, "months": window, "has_prior": prior is not None,
                "kpis": kpis_for_window(ing, window, prior)}
        view["trees"] = _view_trees(trees, window, prior, full)
        if not full:  # FPA-021/027: el drill Project→Model solo tiene totales
            view["limitation"] = ("el drill Project→Model solo está disponible "
                                  "para el periodo completo (no hay project-by-model por mes)")
        views[key] = view
    return views

# F8 (coffe-5ng): vista de energía estimada con banda de caché

def _energy_month(ym, mo, end):
    """Registro mensual de energía + bandas; None si el fallback no trae
    campos mensuales de energía (7mj sin config)."""
    if "energy_kwh" not in mo:
        return None  # fallback sin config (7mj): sin campos mensuales
    kwh = mo.get("energy_kwh") or 0.0
    band = mo.get("energy_kwh_band") or {"low": 0.0, "high": 0.0}
    no_tel = [f"{model}: {e['reason']}"
              for model, e in (mo.get("energy_kwh_by_model") or {}).items()
              if isinstance(e, dict) and e.get("kwh") is None]
    # FPA-004: meses parciales marcados (días transcurridos / total)
    total_days = month_total_days(ym)
    partial = bool(end) and end[:7] == ym and end[8:10] and \
        int(end[8:10]) < total_days
    elapsed = int(end[8:10]) if partial else total_days
    return ({"ym": ym, "label": month_label(ym), "kwh": kwh, "low": band["low"],
             "high": band["high"], "no_telemetry": no_tel, "partial": partial,
             "elapsed": elapsed, "total_days": total_days},
            kwh, band["low"], band["high"])

def build_energy(report):
    """F8 (coffe-5ng): datos de la vista Energía — kWh mensuales del
    reporte (7mj, nominal con factor del config) + banda de sensibilidad
    de caché (coffe-5ng: low/high con factor 0.0/1.0). Los meses cuyo
    energy_kwh es 0.0 por modelos sin telemetría se listan con las razones
    por modelo (loss visible, FPA-008): nunca se muestran como medición.
    """
    md = report["metadata"]
    factors = (md.get("energy_band_cache_factors")
               if md.get("energy_provenance") == "assumed" else None)
    end = md.get("date_range", {}).get("end")
    months = []
    tot_kwh = tot_low = tot_high = 0.0
    for ym in sorted(report["monthly"].keys()):
        entry = _energy_month(ym, report["monthly"][ym], end)
        if entry is None:
            continue
        months.append(entry[0]); tot_kwh += entry[1]; tot_low += entry[2]; tot_high += entry[3]
    return {"months": months, "provenance": md.get("energy_provenance", "unavailable"),
            "total": {"kwh": round(tot_kwh, 3), "low": round(tot_low, 3),
                      "high": round(tot_high, 3)},
            "factors": factors, "nominal_factor": md.get("energy_cache_read_factor")}

def energy_bars_svg(energy, w=560, h=190):
    """F8 (coffe-5ng): barras mensuales con banda low–high y marcador
    del nominal dentro. SVG inline determinista; cada bar lleva el
    contrato del readout (tabindex/aria-label/data-label, FPA-175)."""
    ms = [m for m in energy["months"] if m["low"] > 0 or m["high"] > 0]
    if not ms:
        return ""
    hi = max(m["high"] for m in ms) or 1.0
    n = len(ms)
    bw = min(60.0, (w - 30) / n - 14)
    gap = (w - 20 - n * bw) / max(n - 1, 1)
    Y = lambda v: h - 26 - v / hi * (h - 48)  # noqa: E731
    parts = []
    x = 12.0
    for m in ms:
        pm = (" (parcial: {}/{} días)".format(m["elapsed"], m["total_days"])
              if m.get("partial") else "")
        label = (f"{m['label']}: {m['kwh']:.3f} kWh "
                 f"(banda {m['low']:.3f}–{m['high']:.3f}){pm}")
        parts.append(
            f'<rect class="enb" x="{x:.1f}" y="{Y(m["high"]):.1f}" width="{bw:.1f}" '
            f'height="{max(Y(m["low"]) - Y(m["high"]), 1.0):.1f}" tabindex="0" role="img" '
            f'aria-label="{esc_html(label)}" data-label="{esc_html(m["label"])}" '
            f'data-value="{esc_html(f"{m["kwh"]:.3f}")}"/>')
        parts.append(
            f'<line class="enbn" x1="{x:.1f}" x2="{x + bw:.1f}" y1="{Y(m["kwh"]):.1f}" '
            f'y2="{Y(m["kwh"]):.1f}"/>')
        parts.append(
            f'<text x="{x + bw / 2:.1f}" y="{Y(m["high"]) - 4:.1f}" class="enbt" '
            f'text-anchor="middle">{m["kwh"]:.1f}</text>')
        parts.append(
            f'<text x="{x + bw / 2:.1f}" y="{h - 8:.1f}" class="enbl" text-anchor="middle">{m["label"]}</text>')
        x += bw + gap
    return (f'<svg class="enb-chart chart" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" role="img" aria-label="Energía estimada por mes con banda de caché">'
            f'{"".join(parts)}</svg>')

def energy_html(energy):
    """F8 (coffe-5ng): sección de la vista Energía — KPI total con banda,
    chart mensual, caption de extremos y meses sin telemetría con razón.
    FPA-008: sin config de energía (fallback) muestra n/a con razón,
    nunca un 0 que parezca medición."""
    if energy["provenance"] != "assumed":
        reason = ("sin config de energía (fallback): sin coeficientes "
                  "versionados no hay estimación")
        na = fig('<span class="na">n/a</span>', energy["provenance"])
        return (f'\n  <p class="finding">{na}</p>'
                f'\n  <p class="finding-meta">{reason}</p>')
    t = energy["total"]
    kpi = (f"{t['kwh']:.1f} kWh (banda {t['low']:.1f}–{t['high']:.1f})")
    parciales = [m for m in energy["months"] if m.get("partial")]
    nota_par = ""
    if parciales:
        pm = ", ".join(f"{m['label']} ({m['elapsed']}/{m['total_days']} días)"
                       for m in parciales)
        nota_par = (f" Meses parciales sin anualizar: {pm} — el total "
                    "acumula lo transcurrido, no proyecta fin de mes.")
    cap = ("Estimación con coeficientes de laboratorio (assumed, nunca medición). "
           "La banda muestra los extremos de sensibilidad al acierto de caché: "
           "low = 0% y high = 100% de los tokens de cache_read servidos desde "
           "caché; la marca en cada barra es el "
           f"escenario base (factor nominal {energy['nominal_factor']})."
           + nota_par)
    no_tel = [(m["label"], m["no_telemetry"]) for m in energy["months"] if m["no_telemetry"]]
    no_tel_html = ("<details class=\"tree\" id=\"energy-no-telemetry\">"
                   "<summary><h3>Meses sin telemetría de tokens</h3></summary>"
                   + "".join(f"<p class=\"small\"><strong>{lbl}</strong>: "
                             + "; ".join(reasons) + "</p>" for lbl, reasons in no_tel)
                   + "</details>") if no_tel else ""
    return f"""
  <p class="finding">{fig(f'<span class="val">{kpi}</span>', energy["provenance"])}</p>
  <p class="finding-meta">total del periodo, banda de sensibilidad de caché incluida</p>
  {energy_bars_svg(energy)}
  <p class="small">{cap}</p>
  {no_tel_html}"""

# add-session-spend-report: vista "Gasto diario" con drill-down por sesión
from viz_fpa_common import _link  # noqa: E402

_link(globals())
