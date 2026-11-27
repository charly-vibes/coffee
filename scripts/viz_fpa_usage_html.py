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


def _longest_rows(s):
    """Filas de sesiones más largas (FPA-116) + toggle top5."""
    long_raw = "".join(
        f'<tr><td>{fmt_int(x["turns"])}</td><td>{esc_html(x["date"] or "")}</td>'
        f'<td>{esc_html(x["project"] or "")}</td>'
        f'<td>{fmt_usd(x["cost"]) if x["cost"] is not None else _na_cell(x["cost_reason"])}</td></tr>'
        for x in s["longest"])
    return top5_rows(long_raw)


def _clear_bits(s):
    """Texto y scope de /clear por 100 sesiones (FPA-118)."""
    clear = (f"{s['clear_per_100']:.1f}" if s["clear_per_100"] is not None
             else "n/a")
    clear_scope = (" (claude-only)" if s["clear_per_100"] is not None
                   else f' — {_na_cell(s["clear_scope_reason"])}')
    return clear, clear_scope


def _na_or(value, reason):
    """Valor si existe, si no celda n/a con razón (FPA-008)."""
    return value if value else _na_cell(reason)


def sessions_html(usage):
    """FPA-116…118: buckets, sesiones largas, coste/mediana/p90 y /clear."""
    s = usage["sessions"]
    bucket_rows = "".join(f'<tr><td>{b["label"]}</td>'
                          f'<td>{fmt_int(b["count"])}</td></tr>'
                          for b in s["buckets"])
    long_rows, show_all = _longest_rows(s)
    clear, clear_scope = _clear_bits(s)
    resets_rows = " · ".join(
        f"{esc_html(t)}: {fmt_int(v.get('count', 0))} ({esc_html(v.get('signal', ''))})"
        for t, v in sorted((s.get("resets_by_tool") or {}).items()))
    return f'''<details class="tree" data-tree="sessions" id="sessions">
<summary><h2>Sesiones</h2></summary>
<table class="small"><caption>Distribución de longitud de sesión (FPA-116)</caption>
<thead><tr><th>Bucket (turns)</th><th>Sesiones</th></tr></thead>
<tbody>{bucket_rows}</tbody></table>
<table class="small"><caption>Sesiones más largas (FPA-116)</caption>
<thead><tr><th>Turns</th><th>Fecha</th><th>Proyecto</th><th>Coste</th></tr></thead>
<tbody>{long_rows}</tbody></table>
{show_all}
<p>Coste por bucket: {_na_or(s["cost_by_bucket"], s["cost_by_bucket_reason"])} · mediana/p90 por sesión: {_na_or(s["median_p90"], s["median_p90_reason"])}</p>
<p>/clear por 100 sesiones (FPA-118){clear_scope}:
{fig(clear, "reported")} · por mes: {_na_or(s["clear_monthly"], s["clear_monthly_reason"])} ·
sesiones > {s["long_turns"]} turns sin /clear: {_na_or(s["long_no_clear"], s["long_no_clear_reason"])}</p>
<p>Resets por tool (coffe-i31, señales distintas por harness): {resets_rows}</p>
</details>'''


def timeline_html(usage):
    """FPA-119: primera/última actividad por tool y model con gaps."""
    tl = usage["timeline"]
    note = (_na_cell(tl["unverified_reason"]) if not tl["verified"]
            else 'fechas verificadas con la timezone del tracker')

    def _table(items, caption):
        raw = "".join(
            f'<tr><td>{esc_html(x["name"])}</td><td>{x["first"]}</td>'
            f'<td>{x["last"]}</td>'
            f'<td>{x["max_gap_days"]}</td>'
            f'<td>{"⚠ gap > " + str(usage["timeline"]["gap_days"]) + " días" if x["flagged"] else "—"}</td></tr>'
            for x in items)
        rows, show_all = top5_rows(raw)
        return (f'<table class="small"><caption>{caption}</caption>'
                '<thead><tr><th>Nombre</th><th>Primera</th><th>Última</th>'
                '<th>Gap máx (días)</th><th>Flag</th></tr></thead>'
                f'<tbody>{rows}</tbody></table>{show_all}')

    return f'''<details class="tree" data-tree="timeline" id="timeline">
<summary><h2>Timeline de tools y models</h2></summary>
{_table(tl["tools"], "Por tool (FPA-119)")}
{_table(tl["models"], "Por model (FPA-119)")}
<p class="small">Umbral de gap: {tl["gap_days"]} días (config, assumed) —
{note}</p>
</details>'''


def _conc_metrics(c):
    """Textos de las tres métricas de concurrencia (FPA-120)."""
    pph = c["projects_per_hour"]
    peak_s = c["peak_simultaneous_sessions"]
    sw = c["switches_per_hour"]
    pph_txt = (f'pico {fmt_int(pph["peak"])}, media {pph["avg"]:.2f}/h'
               if pph.get("peak") is not None else _na_cell(pph.get("reason", "sin datos")))
    peak_txt = (fmt_int(peak_s["peak"]) if peak_s.get("peak") is not None
                else _na_cell(peak_s.get("reason")))
    sw_txt = (f'{sw["value"]:.2f}/h' if sw.get("value") is not None
              else _na_cell(sw.get("reason")))
    return pph, peak_s, sw, pph_txt, peak_txt, sw_txt


def _agent_monthly(a):
    """Tabla o n/a del share mensual de sesiones con Agent (FPA-121)."""
    if a["monthly"]:
        m_rows = "".join(f'<tr><td>{m["ym"]}</td><td>{_share(m["share"])}</td></tr>'
                         for m in a["monthly"])
        return (f'<table class="small"><caption>Sesiones con Agent por mes '
                f'(FPA-121)</caption><thead><tr><th>Mes</th><th>Share</th></tr>'
                f'</thead><tbody>{m_rows}</tbody></table>')
    return f'<p>Trend mensual: {_na_cell(a["monthly_reason"])}</p>'


def concurrency_html(usage):
    """FPA-120/121: concurrencia etiquetada + share de sesiones con Agent."""
    c = usage["concurrency"]
    pph, peak_s, sw, pph_txt, peak_txt, sw_txt = _conc_metrics(c)
    a = usage["agent_share"]
    share = _share(a["share"])
    per_tool = " · ".join(
        f"{esc_html(t)}: {_share(v['share']) if v['share'] is not None else _na_cell(v['reason'])}"
        for t, v in sorted((a.get("by_tool_shares") or {}).items()))
    monthly = _agent_monthly(a)
    return f'''<details class="tree" data-tree="concurrency" id="concurrency">
<summary><h2>Concurrencia y autonomía</h2></summary>
<ul class="small">
<li>Proyectos distintos por hora ({esc_html(pph["measure"])}): {pph_txt}</li>
<li>Pico de sesiones/agentes simultáneos ({esc_html(peak_s["measure"])}): {peak_txt}</li>
<li>Switches de proyecto por hora activa ({esc_html(sw["measure"])}): {sw_txt}</li>
</ul>
<p>Share de sesiones con Agent (FPA-121): {fig(share, "reported")}</p>
<p class="small">Por tool: {per_tool} —
{esc_html(a.get("agent_semantics") or "")}</p>
{monthly}
</details>'''


def lifecycle_html(usage):
    """FPA-122/123: new/active/dormant + coste dormante + activos por mes."""
    lc = usage["lifecycle"]
    raw = "".join(
        f'<tr><td>{esc_html(p["name"])}</td><td>{p["status"]}</td>'
        f'<td>{p["first_seen"]}</td><td>{p["last_seen"]}</td></tr>'
        for p in lc["projects"])
    rows, show_all = top5_rows(raw)
    if lc["active_by_month"]:
        m_rows = "".join(f'<tr><td>{m["ym"]}</td><td>{fmt_int(m["count"])}</td></tr>'
                         for m in lc["active_by_month"])
        monthly = (f'<table class="small"><caption>Proyectos activos por mes '
                   f'(FPA-123)</caption><thead><tr><th>Mes</th><th>Activos</th>'
                   f'</tr></thead><tbody>{m_rows}</tbody></table>')
    else:
        monthly = f'<p>Activos por mes: {_na_cell(lc["active_by_month_reason"])}</p>'
    dormant = lc["dormant"]
    per_project = (fmt_usd(dormant["cost_per_project"])
                   if dormant["cost_per_project"] is not None else "n/a")
    ov = usage["repo_overlay"]
    overlay = ("" if ov["available"]
               else f'<p class="small">Overlay de creación de repos (FPA-098): '
                    f'{_na_cell(ov["reason"])}</p>')
    return f'''<details class="tree" data-tree="lifecycle" id="lifecycle">
<summary><h2>Ciclo de vida de proyectos</h2></summary>
<p class="small">Referencia: {lc["reference"]} · new ≤ {lc["new_days"]} días ·
dormant > {lc["dormant_days"]} días sin actividad (config, assumed)</p>
<table class="small"><caption>Clasificación de proyectos (FPA-122)</caption>
<thead><tr><th>Proyecto</th><th>Estado</th><th>Primera</th><th>Última</th></tr></thead>
<tbody>{rows}</tbody></table>
{show_all}
<p>Coste efectivo dormante (FPA-123): total
{fig(fmt_usd(dormant["cost_total"]), "reported")} ·
por proyecto {fig(per_project, "reported")} ({dormant["count"]} proyectos)</p>
{monthly}
{overlay}
</details>'''


def pareto_html(usage):
    """FPA-028/036: Pareto con cola agrupada y concentración top-3."""
    p = usage["pareto"]
    rows = "".join(
        f'<tr><td>{esc_html(r["name"])}</td><td>{fmt_usd(r["cost"])}</td>'
        f'<td>{_share(r["share"])}</td><td>{_share(r["cumulative_share"])}</td></tr>'
        for r in p["rows"])
    rows_html, show_all = top5_rows(rows)
    tail_html = (f'<tr class="tail"><td>Cola agrupada ({p["tail"]["count"]} '
                 f'proyectos)</td>'
                 f'<td>{fmt_usd(p["tail"]["cost"])}</td>'
                 f'<td>{_share(p["tail"]["share"])}</td>'
                 f'<td>{_share(p["tail"]["cumulative_share"])}</td></tr>'
                 if p["tail"] else
                 '<tr class="tail"><td>Cola agrupada (0 proyectos)</td>'
                 '<td>$0.00</td><td>0.0%</td><td>100.0%</td></tr>')
    title, meta = finding_parts(
        p["top3_share"], "top3_share",
        "50% del coste en top-3 (config FPA-088)",
        "Tres proyectos concentran el {0} del coste efectivo",
        "Pareto de proyectos")
    return f'''<details class="tree" data-tree="pareto" id="pareto">
<summary><h2 class="finding">{esc_html(title)}</h2></summary>
<p class="small finding-meta">Pareto de proyectos · {meta}</p>
<table class="small"><caption>Pareto por coste efectivo (FPA-028; fuera del
top {_PARETO_TOP} la cola se agrupa)</caption>
<thead><tr><th>Proyecto</th><th>Coste</th><th>Share</th><th>Share acum.</th></tr></thead>
<tbody>{rows_html}{tail_html}</tbody></table>
{show_all}
<p>Concentración top-3 (FPA-036): {fig(_share(p["top3_share"]), "reported")}
del coste efectivo · total {fig(fmt_usd(p["total"]), "reported")}</p>
</details>'''


def usage_html(usage):
    """Contenido de la vista Habits (FPA-150): patrones de uso, concurrencia
    y lifecycle, sin wrapper de sección (la vista la provee render_html).
    El Pareto vive en Breakdown (FPA-158) y el timeline en Data & method
    (FPA-157)."""
    return (heatmap_html(usage) + weekly_html(usage)
            + skills_commands_html(usage) + sessions_html(usage)
            + concurrency_html(usage) + lifecycle_html(usage))


# ======================================================================
# coffe-dnx.6: vista Modelos — scorecard de cohorte (coffe-dnx.8 emite
# model_scorecard[] + metadata.scorecard.rule_verdict). Presentación
# solamente: NINGUNA métrica se recalcula aquí (FPA-145).
# ======================================================================
from viz_fpa_common import _link  # noqa: E402

_link(globals())
