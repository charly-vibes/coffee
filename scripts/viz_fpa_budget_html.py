"""Módulo extraído de viz-fpa.py (decomposición, comportamiento idéntico)."""
import calendar  # noqa: F401
import html  # noqa: F401
import json  # noqa: F401
import re  # noqa: F401
import sys
from datetime import date, datetime  # noqa: F401
from html.parser import HTMLParser  # noqa: F401
from pathlib import Path  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fpa_config  # noqa: F401,E402
import schema_validate  # noqa: F401,E402
import site_theme  # noqa: F401,E402




def _budget_row(r):
    """Fila mensual de la tabla de varianza (cells cash/efectivo o n/a)."""
    if r["in_budget"] and r["actual_cash"] is not None:
        cells_cash = (f'<td>{r["actual_display_cash"]}</td>'
                      f'<td>{r["budget_display_cash"]}</td>'
                      f'<td>{_fmt_signed(r["variance_cash"])} '
                      f'{marker_html(r["marker_cash"])}</td>'
                      f'<td>{r["variance_pct_display_cash"]}</td>')
        cells_eff = (f'<td>{r["actual_display_eff"]}</td>'
                     f'<td>{r["budget_display_eff"]}</td>'
                     f'<td>{_fmt_signed(r["variance_eff"])} '
                     f'{marker_html(r["marker_eff"])}</td>'
                     f'<td>{r["variance_pct_display_eff"]}</td>')
    else:  # FPA-008: n/a con razón, nunca vacío
        reason = esc_html(r["reason_cash"] or "")
        na = f'<span class="na" title="{reason}">n/a</span>'
        cells_cash = (f'<td colspan="4">{na} — {reason}</td>' if reason
                      else '<td colspan="4"><span class="na">n/a</span></td>')
        cells_eff = cells_cash
    pr = (f' <span class="small" title="pro-rata FPA-052">'
          f'({r["elapsed"]}/{r["total_days"]})</span>' if r["partial"] else "")
    return (f'<tr data-ym="{r["ym"]}"><td>{r["label"]}{pr}</td>'
            f'<td>{"sí" if r["in_budget"] else "no"}</td>'
            f'{cells_cash}{cells_eff}</tr>')


def _budget_ytd_row(ytd_cash, ytd_eff):
    """Fila YTD de la tabla de varianza (vacía → n/a con razón, FPA-017)."""
    if ytd_cash:
        return (f'<tr class="ytd"><td><strong>YTD</strong></td><td></td>'
                f'<td>{ytd_cash["actual_display"]}</td>'
                f'<td>{ytd_cash["budget_display"]}</td>'
                f'<td>{ytd_cash["variance_display"]} '
                f'{marker_html(ytd_cash["marker"])}</td>'
                f'<td>{ytd_cash["variance_pct_display"]}</td>'
                f'<td>{ytd_eff["actual_display"]}</td>'
                f'<td>{ytd_eff["budget_display"]}</td>'
                f'<td>{ytd_eff["variance_display"]} '
                f'{marker_html(ytd_eff["marker"])}</td>'
                f'<td>{ytd_eff["variance_pct_display"]}</td></tr>')
    return (f'<tr class="ytd"><td><strong>YTD</strong></td>'
            f'<td colspan="9"><span class="na">n/a</span> — sin meses '
            f'dentro del periodo presupuestado</td></tr>')


def budget_html(budget):
    """FPA-050…055: tabla de varianza mensual + YTD + inputs editables.
    El presupuesto de efectivo se muestra como informativo (soft, OQ-1)."""
    rows_html = [_budget_row(r) for r in budget["rows"]]
    ytd_row = _budget_ytd_row(budget["ytd_cash"], budget["ytd_eff"])
    inputs = budget["inputs"]
    return f'''<details class="tree" data-tree="budget" id="budget" open>
<summary><h2>Presupuesto y varianza</h2></summary>
<p class="small">El presupuesto de <strong>efectivo</strong> es informativo
(soft); el gestionado es el de <strong>cash</strong>. Editá los valores —
las varianzas se recalculan sin recargar (FPA-056). Cifras del config:
{prov_tag("assumed")}. El YTD suma solo meses con datos dentro del periodo
presupuestado (los meses sin datos no fabrican actual=0, FPA-017).</p>
<label>Cash $/mes <input class="b-input" id="budget-cash" type="number"
 step="0.01" min="0" value="{inputs["cash_monthly"]}"></label>
<label>Efectivo $/mes <input class="b-input" id="budget-eff" type="number"
 step="0.01" min="0" value="{inputs["effective_monthly"]}"></label>
<label>Objetivo $/1k <input class="b-input" id="budget-target" type="number"
 step="0.01" min="0" value="{inputs["target_per_1k"]}"></label>
<table class="btable">
<thead><tr><th>Mes</th><th>En presupuesto</th>
<th>Cash real</th><th>Budget cash</th><th>Var cash</th><th>Var %</th>
<th>Efectivo real</th><th>Budget efectivo</th><th>Var efectivo</th><th>Var %</th>
</tr></thead>
<tbody>{"".join(rows_html)}{ytd_row}</tbody>
</table>
</details>'''


def _bridge_has_mix(b):
    # señal de mix: el par no es n/a y alguno de los dos meses tiene
    # coste; con $0 en ambos el waterfall es un marco vacío
    return (not b.get("n_a_reason")
            and (b["cost0"] > 0 or b["cost1"] > 0))


def _bridge_figure(b):
    return (f'<figure><h3>{esc_html(b["label"])}</h3>{b["svg"]}'
            f'<figcaption class="small">Δ {fmt_usd(b["delta"])} = Volumen '
            f'{fmt_usd(b["volume"])} + Mix {fmt_usd(b["mix"])} + Rate '
            f'{fmt_usd(b["rate"])} (identidad ≤ $0.01, residuo '
            f'{abs(b["identity_residual"]):.4f})</figcaption></figure>')


def _bridge_na_head(fuera):
    """FPA-008: n/a con razón, sin selector vacío."""
    razon = "ningún mes con datos de mix (coste efectivo por modelo)"
    if fuera:
        razon += " — pares sin señal: " + ", ".join(fuera)
    return f'<p class="f3-nv">n/a — {esc_html(razon)}</p>'


def _bridge_selector_head(mix, fuera):
    """Selector de mes + waterfalls pre-calculados en <template>."""
    # default: último mes con datos de mix (orden de inserción cronológico)
    default = list(mix)[-1]
    opts = "".join(
        f'<option value="{ym}"'
        f'{" selected" if ym == default else ""}>'
        f'{esc_html(b["label"])}</option>' for ym, b in mix.items())
    head = (f'<label class="small" for="bridge-month">Mes '
            f'<select id="bridge-month">{opts}</select></label>'
            f'<div id="bridge-figure"></div>'
            + "".join(f'<template data-bridge-month="{ym}">'
                      f'{_bridge_figure(b)}</template>' for ym, b in mix.items()))
    if fuera:
        head += (f'<p class="small">Fuera del selector (sin datos de '
                 f'mix): {esc_html(", ".join(fuera))}</p>')
    return head


def _bridge_stack_rows(bridge):
    """Filas del mix de modelos por mes (100% stacked)."""
    stack_rows = []
    for s in bridge["mix_stack"].values():
        cells = []
        for i, seg in enumerate(s["segments"]):
            pct = f"{100 * seg['share']:.1f}%"
            title = f"{esc_html(seg['model'])}: {pct} ({esc_html(s['metric'])})"
            cells.append(f'<span class="stackbar" title="{title}" '
                         f'style="width:{100 * seg["share"]:.1f}%; '
                         f'background:var(--acc);opacity:{0.35 + 0.13 * i:.2f}">'
                         f'</span>')
        proxy = (f' <span class="na">(proxy: {esc_html(s["proxy_reason"])})</span>'
                 if s["proxy"] else "")
        stack_rows.append(
            f'<tr><td>{esc_html(s["label"])}</td>'
            f'<td><span class="stackrow">{"".join(cells)}</span>{proxy}</td></tr>')
    return stack_rows


def bridge_html(bridge):
    """FPA-060/067/068 + D6 (coffe-8nw): un único waterfall visible con
    selector de mes (default: último mes con datos de mix). Python
    pre-calcula los waterfalls en <template data-bridge-month> y el JS
    monta el elegido sin recarga. Meses sin datos de mix (par n/a o con
    coste $0 en ambos meses — marco vacío) quedan fuera del selector;
    sin ningún mes con mix → n/a con razón, sin selector vacío (FPA-008).
    """
    mix = {ym: b for ym, b in bridge["pairs"].items() if _bridge_has_mix(b)}
    fuera = [b["label"] for ym, b in bridge["pairs"].items()
             if ym not in mix]
    head = (_bridge_na_head(fuera) if not mix
            else _bridge_selector_head(mix, fuera))
    stack_rows = _bridge_stack_rows(bridge)
    return f'''<details class="tree" data-tree="bridge" id="bridge">
<summary><h2>Bridge precio-volumen-mix (efectivo)</h2></summary>
{head}
<h3>Mix de modelos por mes (100% stacked)</h3>
<table class="small" id="mix-stack">
<tbody>{"".join(stack_rows)}</tbody>
</table>
</details>'''


def _forecast_plan_opts(fc):
    return "".join(
        f'<option value="{esc_html(p["key"])}"'
        f'{" selected" if p["key"] == fc["default_plan"] else ""}>'
        f'{esc_html(p["label"])} (${p["fee"]:,.2f}/mes)</option>'
        for p in fc["plans"])


def _forecast_rows(fc):
    """Fila del resto del mes parcial (FPA-075) + filas forecast △."""
    rows = []
    rem = fc["remainder"]
    if rem:  # FPA-075: resto del mes parcial, fila separada y marcada
        rows.append(
            f'<tr data-fc-ym="{rem["ym"]}"><td>{esc_html(rem["label"])} '
            f'<span class="small">(resto: {rem["elapsed"]}/{rem["total_days"]})</span></td>'
            f'<td>{fmt_usd(rem["eff"])} {marker_html(rem["marker"])}</td>'
            f'<td>{fmt_usd(rem["cash"])} {marker_html(rem["marker"])}</td></tr>')
    for r in fc["rows"]:
        rows.append(
            f'<tr data-fc-ym="{r["ym"]}"><td>{esc_html(r["label"])}</td>'
            f'<td>{fmt_usd(r["eff"])} {marker_html(r["marker"])}</td>'
            f'<td>{fmt_usd(r["cash"])} {marker_html(r["marker"])}</td></tr>')
    return rows


def _forecast_outlook(out):
    """FPA-074: YTD real + outlook vs presupuesto, con varianza."""
    return (f'<h3>Outlook: YTD + forecast vs presupuesto'
            f' (hasta {esc_html(out["ym_to"] or "")})</h3>'
            f'<table class="fc-tbl"><tbody>'
            f'<tr><td>Real YTD cash</td><td>{fmt_usd(out["actual_cash"])}</td></tr>'
            f'<tr><td>Proyección cash (YTD + outlook)</td>'
            f'<td>{fmt_usd(out["projected_cash"])} '
            f'{marker_html(out["marker_cash"])}</td></tr>'
            f'<tr><td>Budget cash</td><td>{fmt_usd(out["budget_cash"])}</td></tr>'
            f'<tr><td>Varianza cash</td><td>{_fmt_signed(out["variance_cash"])} '
            f'({_fmt_pct_signed(out["variance_pct_cash"])}) '
            f'{marker_html(out["marker_cash"])}</td></tr>'
            f'<tr><td>Real YTD efectivo</td><td>{fmt_usd(out["actual_eff"])}</td></tr>'
            f'<tr><td>Proyección efectivo (YTD + outlook)</td>'
            f'<td>{fmt_usd(out["projected_eff"])} '
            f'{marker_html(out["marker_eff"])}</td></tr>'
            f'<tr><td>Budget efectivo (informativo)</td>'
            f'<td>{fmt_usd(out["budget_eff"])}</td></tr>'
            f'<tr><td>Varianza efectivo</td><td>{_fmt_signed(out["variance_eff"])} '
            f'({_fmt_pct_signed(out["variance_pct_eff"])}) '
            f'{marker_html(out["marker_eff"])}</td></tr>'
            f'</tbody></table>')


def forecast_html(fc):
    """FPA-070…077: forecast con escenarios editables (update sin reload),
    filas forecast marcadas con △ (no solo color) y outlook vs presupuesto."""
    if fc.get("n_a_reason"):  # FPA-008: nunca forecast inventado
        return (f'<details class="tree" data-tree="forecast" '
                f'id="forecast" open>\n'
                f'<summary><h2>Forecast y outlook</h2></summary>\n'
                f'<p class="f3-nv">n/a — {esc_html(fc["n_a_reason"])}</p>\n'
                f'</details>')
    base = fc["base"]
    plan_opts = _forecast_plan_opts(fc)
    rows = _forecast_rows(fc)
    outlook = _forecast_outlook(fc["outlook"])
    return f'''<details class="tree" data-tree="forecast" id="forecast" open>
<summary><h2>Forecast y outlook</h2></summary>
<p class="small">Run-rate base = media FME de los últimos 3 meses con datos
({", ".join(base["months_used"])}). Escenario default pre-calculado en
Python; los cambios se recalculan sin recargar (FPA-077). Cifras forecast:
{prov_tag("assumed")}.</p>
<label>Crecimiento mensual % <input class="fc-input" id="fc-growth" type="number"
 step="0.5" value="0"></label>
<label>Cambio de rate % <input class="fc-input" id="fc-rate" type="number"
 step="0.5" value="0"></label>
<label>Plan futuro <select id="fc-plan">{plan_opts}</select></label>
<table class="fc-tbl">
<thead><tr><th>Mes</th><th>Efectivo</th><th>Cash</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
{outlook}
</details>'''


# ======================================================================
# coffe-6lz F4: jerarquía tipográfica — FPA-ids fuera del cuerpo visible
# ======================================================================

# Referencia a spec: FPA-120b, FPA-116/117, FPA-061…063, FPA-028/036
from viz_fpa_common import _link  # noqa: E402

_link(globals())
