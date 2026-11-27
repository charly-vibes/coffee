"""Módulo extraído de viz-fpa.py (decomposición, comportamiento idéntico)."""
import calendar  # noqa: F401
import dataclasses
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




_DOW_NAMES = {1: "lun", 2: "mar", 3: "mié", 4: "jue", 5: "vie",
              6: "sáb", 7: "dom"}


def _wh_text(wh):
    """Horario laboral del config como texto (para el threshold visible)."""
    days = sorted(wh.get("days", []))
    names = (f"{_DOW_NAMES.get(days[0], '?')}–"
             f"{_DOW_NAMES.get(days[-1], '?')}" if days else "n/a")
    return f"{names} {wh.get('start', '?')}–{wh.get('end', '?')}"


def prov_tag(kind):
    """Tag visible de procedencia: reported / assumed."""
    label = "reported" if kind == "reported" else "assumed"
    return f'<span class="prov-tag" title="procedencia: {kind}">{label}</span>'


def kpi_card(k):
    """Card de KPI con sparkline SVG, delta y provenance (FPA-030…045)."""
    if k["value"] is None:  # FPA-042: n/a con razón, nunca cero/infinito
        value = fig('<span class="na">n/a</span>', k["provenance"])
        sub = f'<p class="reason">{k["reason"]}</p>'
    else:
        value = fig(f'<span class="val">{k["display"]}</span>', k["provenance"])
        d = k.get("delta_display")
        # coffe-esc F3: la interpretación de una línea de la headline (si el
        # KPI tiene una) pasa al contexto del card, junto al delta.
        ctx = k.get("headline_interp") or ""
        sub = (f'<p class="interp"'
               + (' title="FPA-045"' if k.get("excluded_share") else "")
               + f'>Δ vs prior: {d or "n/a"}'
               + (f' <span class="small">(tasa diaria)</span>'
                  if k.get("delta_kind") == "daily-rate" and d else "")
               + (f' · excluido por tokens: {k["excluded_display"]}'
                  if k.get("excluded_share") else "")
               + (f' — {ctx}' if ctx else "")
               + "</p>")
    return (f'<div class="headline kpi" data-key="{k["key"]}">'
            f'<h3>{k["label"]}</h3>{value}{sub}'
            f'{sparkline_svg(k.get("spark"))}</div>')


def sparkline_svg(series, w=120, h=28):
    """FPA-030: sparkline mensual como SVG inline determinista."""
    vals = [p["value"] for p in (series or [])]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    step = w / (len(vals) - 1)
    pts = " ".join(
        f"{i * step:.1f},{h - 2 - (v - lo) / span * (h - 4):.1f}"
        for i, v in enumerate(vals))
    return (f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="sparkline"><polyline fill="none" '
            f'stroke="currentColor" stroke-width="1.5" points="{pts}"/></svg>')


def _tree_footnote(is_time):
    if not is_time:
        return ""
    return ('<p class="small">* presupuesto cash del config (assumed). '
            'En ventanas que mezclan meses fuera del periodo presupuestado, '
            'la varianza compara solo el tramo con presupuesto (coffe-9p4).</p>')


@dataclasses.dataclass(frozen=True)
class TreeSectionOpts:
    """Opciones de la sección de árbol."""
    is_time: bool = False
    open_: bool = False

def tree_section(name, title, root, opts=None):
    """Sección de árbol expandible (FPA-020/022) con columnas pre-formateadas."""
    o = opts or TreeSectionOpts()
    cols = ("Nodo", "Coste", "% total", "Interacciones", "$/1k", "Δ vs prior")
    if o.is_time:
        cols += ("Presupuesto*", "Varianza", "Var %")
    head = "".join(f"<th>{c}</th>" for c in cols)
    rows = _tree_html(root, o.is_time)
    open_attr = " open" if o.open_ else ""
    return (f'<details class="tree" data-tree="{name}"{open_attr}>'
            f'<summary><h2>{title}</h2></summary>'
            f'<table class="ttree"><thead><tr>{head}</tr></thead>'
            f'<tbody>{rows}</tbody></table>'
            f'{_tree_footnote(o.is_time)}'
            f'</details>')


def _repo_anchor(row):
    """coffe-vp8: label con ancla al repo GH y 🔒 si es privado."""
    label = row["label"]
    if not row.get("url"):
        return esc_html(label)
    anchor = (f'<a class="repo-link" href="{esc_html(row["url"])}" '
              f'target="_blank" rel="noopener noreferrer">{esc_html(label)}</a>')
    if row.get("private"):
        anchor += (' <span class="lock" title="repo privado" '
                   'aria-label="repo privado">🔒</span>')
    return anchor


def _tree_html(row, is_time=False, depth=0):
    cells = (f'<td>{row["cost_display"]}</td><td>{row["pct_display"]}</td>'
             f'<td>{row["interactions_display"]}</td><td>{row["per_1k_display"]}</td>'
             f'<td>{row["delta_display"]}</td>')
    if is_time:
        cells += (f'<td>{row["budget_display"]}</td>'
                  f'<td>{row["variance_display"]}</td>'
                  f'<td>{row["variance_pct_display"]}</td>')
    kids = "".join(_tree_html(c, is_time, depth + 1) for c in row["children"])
    if kids:
        label = (f'<details open><summary class="tlabel">{_repo_anchor(row)}</summary>'
                 f'</details>')
    else:
        label = (f'<span style="display:inline-block;margin-left:{depth * 14}px">'
                 f'{_repo_anchor(row)}</span>')
    return f'<tr class="trow" data-key="{row["key"]}"><td>{label}</td>{cells}</tr>{kids}'


def _kind_rows(notes):
    ks = notes["kinds"]
    total_kinds = sum(ks.values())
    return "".join(
        f'<tr><td>{k}</td><td>{fmt_int(v)}</td>'
        f'<td>{(f"{100 * v / total_kinds:.1f}%" if total_kinds else "n/a")}</td></tr>'
        for k, v in sorted(ks.items(), key=lambda x: -x[1]))


def _tz_cell(notes):
    if notes["unverified_time"]:  # FPA-142
        return ('<span class="na">n/a</span> — ' + notes["timezone_missing_reason"])
    return fig(notes["timezone"], "reported")


def _conc_text(notes):
    conc = notes["concurrency"] or {}
    dp = conc.get("distinct_projects_per_hour") or {}
    ps = conc.get("peak_simultaneous_sessions") or {}
    sw = conc.get("project_switches_per_active_hour") or {}
    ps_peak = (fmt_int(ps["peak"]) if ps.get("peak") is not None
               else 'n/a — ' + (ps.get("reason") or "sin datos"))
    return (f'<p>Concurrencia — proyectos distintos/hora ({dp.get("measure", "n/a")}): '
            f'pico {fmt_int(dp.get("peak", 0))},\n'
            f'     media {dp.get("avg", 0)}/h · pico de sesiones simultáneas ({ps.get("measure", "parallel-agent")}):\n'
            f'     {ps_peak} · switches de proyecto/hora activa ({sw.get("measure", "human-context-switching")}): {sw.get("value", "n/a")}</p>')


def data_notes_html(notes):
    """Sección Data: definición, kinds, share filtrado, timezone, concurrencia."""
    kind_cells = _kind_rows(notes)
    return f'''<div class="data-notes">
  <h3>Datos y definiciones</h3>
  <p>{notes["interaction_definition"]}</p>
  <table class="small"><caption>Interacciones por kind</caption>
    <thead><tr><th>kind</th><th>Eventos</th><th>Share</th></tr></thead>
    <tbody>{kind_cells}</tbody></table>
  <p>Share de tool calls: {share_fig(notes["tool_call_share"])} —
     share excluido por el filtro charly: {share_fig(notes["filtered_share"])}
     ({fmt_int(notes["filtered_interactions"])} interacciones).</p>
  <p>Timezone de bucketing hora/día: {_tz_cell(notes)}</p>
  {_conc_text(notes)}
</div>'''


def share_fig(v):
    return fig(f"{100 * v:.1f}%", "reported") if v is not None else \
        '<span class="na">n/a</span>'


def alerts_html(alerts):
    """FPA-080: lista de alertas con severity, regla y evidencia.
    Sin alertas → estado visible "sin alertas", nunca sección vacía."""
    if not alerts:
        return ('<details class="tree" data-tree="alerts" id="alerts">'
                '<summary><h2>Alertas</h2>'
                '</summary><p class="small" id="alerts-none">Sin alertas — '
                'ninguna regla se disparó con los umbrales del config '
                '(FPA-088).</p></details>')
    sev_cls = {"high": "sev-high", "medium": "sev-medium", "low": "sev-low"}
    items = []
    for a in alerts:
        ev = ", ".join(f"{k}: {v}" for k, v in sorted(a["evidence"].items()))
        action = ""
        if a.get("action"):
            action = (f'<a class="cta alert-action" '
                      f'href="{esc_html(a["action"]["target"])}">'
                      f'{esc_html(a["action"]["label"])}</a>')
        items.append(
            f'<li class="alert {sev_cls.get(a["severity"], "")}" '
            f'data-rule="{esc_html(a["rule"])}">'
            f'<span class="sev">{esc_html(a["severity"])}</span> '
            f'<strong>{esc_html(a["rule"])}</strong> — '
            f'{esc_html(a["message"])}'
            f'<span class="small">Evidencia: {esc_html(ev)}</span>'
            f'{action}</li>')
    return (f'<details class="tree" data-tree="alerts" id="alerts">'
            f'<summary>'
            f'<h2>Alertas <span class="small">({len(alerts)})</span></h2>'
            f'</summary><ul class="alert-list">{"".join(items)}</ul>'
            f'</details>')


def _plan_util(p):
    if p["utilization"] is not None:  # FPA-008: n/a con razón
        return f'{p["utilization"] * 100:.1f}%'
    return (f'<span class="na" '
            f'title="{esc_html(p["utilization_reason"] or "")}">n/a'
            f'</span> — {esc_html(p["utilization_reason"] or "")}')


def _plan_rows(econ):
    rows = []
    for p in econ["plans"]:
        util = _plan_util(p)
        head = (fmt_usd(p["headroom"]) if p["headroom"] is not None
                else '<span class="na">n/a</span>')
        eff = (fmt_usd(p["eff_cost"]) if p["eff_cost"] is not None
               else '<span class="na">n/a</span>')
        rows.append(
            f'<tr><td>{esc_html(p["tool"])}</td>'
            f'<td>{esc_html(p["label"])}</td>'
            f'<td>{esc_html(p["start"])} → {esc_html(p["end"] or "vigente")}</td>'
            f'<td>{fmt_usd(p["monthly_fee"])}</td>'
            f'<td>{fmt_usd(p["price"])}</td>'
            f'<td>{eff}</td><td>{util}</td>'
            f'<td>{fmt_usd(p["break_even"])}</td><td>{head}</td>'
            f'<td>{prov_tag("assumed")}</td></tr>')
    return rows


def _case_rows(fc):
    cases = (("actual (calendario vigente)", fc["actual"]),
             ("todo pay-per-token", fc["todo_pay_per_token"]),
             ("todo Pro", fc["todo_pro"]),
             ("todo Max", fc["todo_max"]))
    return "".join(
        f'<tr><td>{esc_html(label)}</td><td>{fmt_usd(val)}</td>'
        f'<td>{prov_tag("assumed")}</td></tr>' for label, val in cases)


def plan_economy_html(econ):
    """FPA-130…133: paneles por plan (utilización, break-even, headroom) +
    comparación de 4 casos + disclaimer de usage limits."""
    rows = _plan_rows(econ)
    fc = econ["four_cases"]
    case_rows = _case_rows(fc)
    note = (f'<p class="small">Nota: {esc_html(econ["four_cases_note"])}.</p>'
            if econ.get("four_cases_note") else "")
    return f'''<details class="tree" data-tree="plan-economy" 
id="plan-economy">
<summary><h2>Economía de suscripción</h2></summary>
<p class="small">{esc_html(econ["usage_limits_disclaimer"])}</p>
<table class="btable">
<thead><tr><th>Tool</th><th>Plan</th><th>Periodo</th><th>Cuota/mes</th>
<th>Precio periodo</th><th>Efectivo</th><th>Utilización</th>
<th>Break-even</th><th>Headroom</th><th>Provenance</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
<h3 class="small">Cash del periodo bajo 4 casos (FPA-132)</h3>
<table class="btable" id="four-cases">
<thead><tr><th>Caso</th><th>Cash</th><th>Provenance</th></tr></thead>
<tbody>{case_rows}</tbody>
</table>
{note}
</details>'''
from viz_fpa_common import _link  # noqa: E402

_link(globals())
