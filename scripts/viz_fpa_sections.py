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




# `_share` métrica del original (viz-fpa.py:1502); el copiador local la
# sombra tras `_share(...)`, así que re-bind al helper de economía.


def _recon_na(rec):
    """Sección n/a cuando la provenance no es reported (FPA-008)."""
    reason = esc_html(rec.get("reason") or "n/a")
    return (f'<details class="tree" data-tree="reconciliation" '
            f'id="reconciliation"><summary>'
            f'<h2>Cash real y reconciliación (FPA-082)</h2></summary>'
            f'<p class="f3-nv">n/a — {reason}</p></details>')


def _provider_cell(p):
    if p["total_in_period"] is not None:
        detalle = " · ".join(
            f"{esc_html(month_label(ym))}: {fmt_usd(v)}"
            for ym, v in sorted(p["months"].items()) if v)
        return (f'{fmt_usd(p["total_in_period"])} '
                f'{prov_tag("reported")}'
                f'<span class="small">{" — " + detalle if detalle else ""}</span>')
    return (f'<span class="na" title="{esc_html(p["n_a_reason"] or "")}">'
            f'n/a</span> — {esc_html(p["n_a_reason"] or "")}')


def _provider_rows(rec):
    return "".join(f'<tr><td>{esc_html(p["provider"])}</td>'
                   f'<td>{_provider_cell(p)}</td></tr>'
                   for p in rec["providers"])


def _nonzero_rows(rec):
    return [r for r in rec["rows"]
            if r["charges_real"] or r["subscription_fee_implicit"]
            or r["cost_effective"]]


def _recon_row_rows(rec):
    return "".join(
        f'<tr data-ym="{r["ym"]}"><td>{esc_html(month_label(r["ym"]))}'
        f' <span class="small">({esc_html(r["ym"])})</span></td>'
        f'<td>{esc_html(r["tool"])}</td>'
        f'<td>{fmt_usd(r["charges_real"])} {prov_tag("reported")}</td>'
        f'<td>{fmt_usd(r["subscription_fee_implicit"])} '
        f'{prov_tag("assumed")}</td>'
        f'<td>{fmt_usd(r["cost_effective"])} {prov_tag("assumed")}</td>'
        f'<td>{fmt_usd(r["fee_month_total"])}</td></tr>'
        for r in _nonzero_rows(rec))


def _recon_tables(rec):
    filas = _nonzero_rows(rec)
    omitidas = len(rec["rows"]) - len(filas)
    nota = (f'<p class="small">{omitidas} filas todo-cero omitidas '
            f'(el detalle completo queda en el JSON embebido del dashboard).</p>'
            if omitidas else "")
    tabla_prov = (_provider_rows(rec) or
                  '<tr><td colspan="2"><span class="na">n/a</span> — sin '
                  'proveedores en el ledger</td></tr>')
    tabla_rows = (_recon_row_rows(rec) or
                  '<tr><td colspan="6"><span class="na">n/a</span> — la '
                  'reconciliación no está disponible en este reporte '
                  '(regenerar con scripts/usage-tracker.py)</td></tr>')
    return tabla_prov, tabla_rows, nota


def reconciliation_html(rec):
    """coffe-a31.3 (CRG-F2): HTML de la sección de reconciliación FPA-082.

    Dos tablas: cash real por proveedor (*reported*, con n/a con razón
    para proveedores sin facturas en el periodo, FPA-008) y la matriz por
    tool y mes (real pagado vs fee implícito vs efectivo estimado). Las
    filas todo-cero de la matriz se omiten con nota visible (el detalle
    completo queda en el JSON embebido)."""
    if rec.get("provenance") != "reported":
        return _recon_na(rec)
    tabla_prov, tabla_rows, nota = _recon_tables(rec)
    return f'''<details class="tree" data-tree="reconciliation" 
id="reconciliation">
<summary><h2>Cash real y reconciliación (FPA-082)</h2></summary>
<p class="small">Cash real = cargos reales del ledger
({esc_html(rec["source"])}) {prov_tag("reported")}; los fees implícitos del
calendario {prov_tag("assumed")} NO son cash: solo reconciliación y economía
de planes. El efectivo estimado {prov_tag("assumed")} es otra medida:
jamás se suma al cash (FPA-002).</p>
<h3 class="small">Cash real por proveedor (periodo del reporte)</h3>
<table class="btable" id="charges-providers">
<thead><tr><th>Proveedor</th><th>Cash periodo (*reported*)</th></tr></thead>
<tbody>{tabla_prov}</tbody>
</table>
<h3 class="small">Reconciliación por tool y mes</h3>
<table class="btable" id="reconciliation-rows">
<thead><tr><th>Mes</th><th>Tool</th><th>Real pagado</th>
<th>Fee implícito</th><th>Efectivo estimado</th><th>Fee total mes</th>
</tr></thead>
<tbody>{tabla_rows}</tbody>
</table>
{nota}
</details>'''


# ======================================================================
# F5: HTML de patrones de uso, concurrencia y lifecycle
# ======================================================================

def _na_cell(reason):
    """Celda n/a con razón (FPA-008), nunca vacía."""
    return f'<span class="na">n/a</span> — {esc_html(reason)}'


def _provenance_note(usage):
    hm = usage["heatmap"]
    if not hm["verified"]:
        return f'<p class="small">Vistas hora/día unverified: {esc_html(hm["unverified_reason"])}</p>'
    return f'<p class="small">Timezone de bucketing: <strong>{esc_html(hm["timezone"])}</strong></p>'


def heatmap_html(usage):
    """FPA-110/142: matriz día×hora como tabla con intensidad; timezone
    etiquetada o marca unverified."""
    hm = usage["heatmap"]
    peak = max((max(row) for row in hm["grid"]), default=0) or 1
    head = "".join(f"<th>{h}</th>" for h in range(24))
    rows = []
    for dow, vals in enumerate(hm["grid"]):
        cells = []
        for n in vals:
            if n:
                alpha = 0.15 + 0.75 * n / peak
                cells.append(f'<td style="background:rgba(138,43,30,{alpha:.2f})" '
                             f'title="{n} interacciones">{n}</td>')
            else:
                cells.append("<td></td>")
        rows.append(f'<tr><th scope="row">{_DOW_SHORT[dow]}</th>{"".join(cells)}</tr>')
    rh = usage["rhythm"]
    title, meta = finding_parts(
        rh["after_hours_share"], "after_hours_share",
        f"fuera de {rh['working_hours_text']} (config, assumed)",
        "El {0} del uso ocurre fuera del horario laboral",
        "After-hours y weekend")
    return f'''<details class="tree" data-tree="heatmap" open id="heatmap">
<summary><h2 class="finding">{esc_html(title)}</h2></summary>
<p class="small finding-meta">Heatmap día×hora · {meta}</p>
{_provenance_note(usage)}
<table class="small heatmap"><caption>Interacciones por día de semana y hora
(día = isoweekday, lunes arriba)</caption>
<thead><tr><th></th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>
<p>After-hours (fuera del horario laboral del config, assumed):
{fig(_share(usage["rhythm"]["after_hours_share"]), "reported")} ·
weekend: {fig(_share(usage["rhythm"]["weekend_share"]), "reported")}</p>
</details>'''


def weekly_html(usage):
    """FPA-112: tabla semanal con WoW y varianza de semanas completas."""
    rhythm = usage["rhythm"]
    rows = []
    for w in rhythm["weeks"]:
        tag = " (parcial)" if w["partial"] else ""
        wow_i = _fmt_pct_signed(w["wow_interactions"])
        wow_c = _fmt_pct_signed(w["wow_cost"])
        rows.append(f'<tr><td>{w["start"]}{tag}</td>'
                    f'<td>{fmt_int(w["interactions"])}</td>'
                    f'<td>{fmt_usd(w["cost_effective"])}</td>'
                    f'<td>{wow_i}</td><td>{wow_c}</td></tr>')
    var = rhythm["variance"]
    if var["interactions"] is None:
        var_txt = _na_cell(var["reason"])
    else:
        var_txt = (f'int {var["interactions"]:.1f} · coste '
                   f'{fmt_usd(var["cost_effective"])} '
                   f'({var["weeks_count"]} semanas completas)')
    return f'''<details class="tree" data-tree="weekly" id="weekly">
<summary><h2>Semanal: WoW y varianza</h2></summary>
<table class="small"><caption>Serie semanal ISO (solo semanas con datos)</caption>
<thead><tr><th>Semana (inicio)</th><th>Interacciones</th><th>Coste efectivo</th>
<th>WoW int</th><th>WoW coste</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
<p>Varianza de valores semanales (reported): {var_txt}</p>
</details>'''


def _skills_inner(usage):
    """Contenido de skills: top, once-uso, zero-uso y trend (FPA-113/114)."""
    s = usage["skills"]
    top_rows, show_all = top5_rows("".join(
        f'<tr><td>{esc_html(x["name"])}</td>'
        f'<td>{fmt_int(x["uses"])}</td></tr>' for x in s["top"]))
    once = ", ".join(s["once"]) if s["once"] else 'n/a — sin skills de un solo uso'
    zero = (", ".join(s["zero"]) if s["zero"] else _na_cell(s["zero_reason"]))
    trend = s["trend"] if s["trend"] else _na_cell(s["trend_reason"])
    return (f'<table class="small"><caption>Skills más usadas (FPA-113)</caption>'
            f'<thead><tr><th>Skill</th><th>Usos</th></tr></thead>'
            f'<tbody>{top_rows}</tbody></table>{show_all}'
            f'<p>Usadas exactamente una vez (FPA-114): {esc_html(once)} · '
            f'sin uso: {zero} · trend mensual: {trend}</p>')


def _commands_inner(usage):
    """Contenido de slash commands (FPA-115)."""
    c = usage["commands"]
    top_rows, show_all = top5_rows("".join(
        f'<tr><td>{esc_html(x["name"])}</td>'
        f'<td>{fmt_int(x["uses"])}</td></tr>' for x in c["top"]))
    trend = c["trend"] if c["trend"] else _na_cell(c["trend_reason"])
    return (f'<table class="small"><caption>Comandos más ejecutados '
            f'(FPA-115)</caption>'
            f'<thead><tr><th>Comando</th><th>Ejecuciones</th></tr></thead>'
            f'<tbody>{top_rows}</tbody></table>{show_all}'
            f'<p>Trend mensual: {trend}</p>')


def skills_commands_html(usage):
    """FPA-156: skills y comandos en una sola vista con toggle."""
    # coffe-dqz: sin open por defecto — la sección primaria de Habits es el
    # heatmap (D5); skills-commands abierto dejaba la vista > 2 viewports a
    # 1280x900 tras el toggle a todo ancho (F1).
    return f'''<details class="tree" data-tree="skills-commands" id="skills-commands">
<summary><h2>Skills y comandos</h2></summary>
<div class="panel-toggle">
<button class="ptoggle active" data-panel="skills-panel" type="button">Skills</button>
<button class="ptoggle" data-panel="commands-panel" type="button">Comandos</button>
</div>
<div id="skills-panel">{_skills_inner(usage)}</div>
<div id="commands-panel" hidden>{_commands_inner(usage)}</div>
</details>'''
from viz_fpa_common import _link  # noqa: E402

_link(globals())
