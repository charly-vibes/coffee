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




CSS = (site_theme.base_css() + site_theme.STRUCTURE_CSS
       + site_theme.DISCLOSURE_CSS + (
       "\n* { box-sizing: border-box; }\nbody { margin:0; background:var(--bg); color:var(--fg); line-height:1.5; }\nheader.site, footer.site { border-bottom: 1px solid var(--line);\n"
       "  max-width: 960px; margin: 0 auto; padding: .8rem 1rem; }\nfooter.site { border:0; border-top: 1px solid var(--line); }\nheader.site h1 { margin: 0; font-size: 1.35rem; }\n"
       "header.site .meta, .small { color: var(--muted); font-size: .82rem; }\n.skip { position:absolute; left:-9999px; }\n.skip:focus { left:.5rem; top:.5rem; background:var(--card); padding:.4rem;\n"
       "  border:1px solid var(--line); }\n.banner-partial { background: var(--card); border: 2px outset #fff;\n  padding: .5rem .8rem; font-size: .85rem; }\n"
       "/* coffe-dnx.6: banner del veredicto de la regla de decisión */\n.banner-verdict { background: var(--card); border: 2px solid var(--line);\n  border-left: 6px solid var(--accent, #8a6d3b); padding: .5rem .8rem;\n"
       "  font-size: .85rem; margin: .6rem 0; }\n#summary { margin: 1rem 0; }\n#summary h2 { margin: 0 0 .2rem; }\n"
       ".headline h3 { margin: 0 0 .3rem; font-size: .8rem; font-weight: 600;\n  color: var(--muted); }\n.fig { font-variant-numeric: tabular-nums; font-size: 1.25rem;\n"
       "  font-weight: 700; display: inline-block; }\n.headline .interp, .headline .reason { margin: .3rem 0 0; font-size: .78rem;\n  color: var(--muted); }\n"
       ".na { color: var(--muted); font-weight: 400; }\n.claims { margin: .6rem 0 1rem; }\n.claim { background: var(--card); border: 1px solid var(--line);\n"
       "  border-left: 4px solid var(--acc); padding: .5rem .7rem; font-size: .85rem; }\n.prov-tag { font-size: .62rem; font-weight: 500; vertical-align: super;\n  padding: 0 .25em; border-radius: 3px; background: var(--line);\n"
       "  color: var(--fg); letter-spacing: .03em; }\n.retro { font-size: .7rem; color: var(--muted); letter-spacing: .08em; }\n.kpi .spark { display: block; margin-top: .3rem; color: var(--acc); }\n"
       "#period-select { font: inherit; margin-left: .4rem; }\n.ttree { width: 100%; border-collapse: collapse; font-size: .85rem;\n  font-variant-numeric: tabular-nums; }\n"
       ".ttree th { text-align: right; padding: .25rem .5rem; border-bottom: 1px solid var(--line);\n  font-weight: 600; color: var(--muted); }\n.ttree th:first-child { text-align: left; }\n"
       ".ttree td { text-align: right; padding: .2rem .5rem; border-bottom: 1px solid var(--line); }\n.ttree td:first-child { text-align: left; }\n.ttree details > summary { cursor: pointer; list-style: none; }\n"
       ".ttree details > summary::before { content: '▸ '; color: var(--muted); }\n.ttree details[open] > summary::before { content: '▾ '; }\n.ttree .repo-link { color: inherit; text-decoration: underline dotted; }\n"
       ".ttree .repo-link:hover { color: var(--accent, #06c); }\n.ttree .lock { font-size: .85em; }\n#data { margin-top: 1.5rem; }\n"
       "#data table { border-collapse: collapse; }\n#data th, #data td { padding: .2rem .7rem; border-bottom: 1px solid var(--line);\n  text-align: left; }\n"
       "#view-limitation { color: var(--muted); }\n.wf .wfb { fill: var(--line); stroke: var(--muted); stroke-width: .5; }\n.wf .wfup { fill: var(--bad); opacity: .75; }\n"
       ".wf .wfdn { fill: var(--ok); opacity: .75; }\n.wf .wft { font-size: 9px; font-variant-numeric: tabular-nums; fill: var(--fg); }\n.wf .wfl { font-size: 9px; fill: var(--muted); }\n"
       "/* F8 (coffe-5ng): vista Energía — banda low–high con marca nominal */\n.enb-chart .enb { fill: var(--line); stroke: var(--muted); stroke-width: .5; }\n.enb-chart .enbn { stroke: var(--acc); stroke-width: 2; }\n"
       ".enb-chart .enbt { font-size: 9px; font-variant-numeric: tabular-nums; fill: var(--fg); }\n.enb-chart .enbl { font-size: 9px; fill: var(--muted); }\n.stackbar { display: inline-block; height: 14px; }\n"
       ".fc-tbl, .btable { border-collapse: collapse; font-size: .85rem;\n  font-variant-numeric: tabular-nums; }\n.fc-tbl th, .btable th { text-align: right; padding: .2rem .5rem;\n"
       "  border-bottom: 1px solid var(--line); color: var(--muted); font-weight: 600; }\n.fc-tbl th:first-child, .btable th:first-child { text-align: left; }\n.fc-tbl td, .btable td { text-align: right; padding: .15rem .5rem;\n"
       "  border-bottom: 1px solid var(--line); }\n.fc-tbl td:first-child, .btable td:first-child { text-align: left; }\n.mk-fav { color: var(--ok); }\n"
       ".mk-unfav { color: var(--bad); }\n.mk-neutral { color: var(--muted); }\n.fc-input, .b-input { font: inherit; width: 5.5em; }\n"
       ".fc-input:invalid, .b-input:invalid { border-color: var(--bad); }\n.f3-nv { color: var(--muted); }\n\n"
       "/* ===== F6: topbar, tabs, CTAs, export, hallazgos, accesibilidad ===== */\n.topbar { position: sticky; top: 0; z-index: 5; background: var(--bg);\n  border-bottom: 1px solid var(--line); max-width: 960px; margin: 0 auto;\n"
       "  padding: .35rem .5rem; display: flex; flex-wrap: wrap; gap: .4rem;\n  align-items: center; }\n.topbar .tabs { display: flex; flex-wrap: wrap; gap: .25rem; }\n"
       ".topbar .tab { display: inline-flex; align-items: center; padding: .4rem .7rem;\n  min-height:44px; color: var(--fg); text-decoration: none;\n  border: 1px solid transparent; font-size: .9rem; }\n"
       ".topbar .tab[aria-current] { border-color: var(--acc); color: var(--acc);\n  font-weight: 600; }\n#period-select { font: inherit; min-height:44px; margin-left: auto; }\n"
       "#share-view { font: inherit; min-height:44px; background: var(--card);\n  border: 2px outset #fff; cursor: pointer; }\nbutton, .cta, .ptoggle, .show-all, .export-csv, .dl-svg {\n"
       "  min-height:44px; font: inherit; cursor: pointer; }\n.cta { display: inline-flex; align-items: center; padding: .4rem .8rem;\n  border: 2px outset #fff; text-decoration: none;\n"
       "  color: var(--fg); background: var(--card); }\n.cta-primary { border-color: var(--acc); color: var(--acc); font-weight: 600; }\n.show-all, .export-csv, .dl-svg { font-size: .8rem; background: var(--card);\n"
       "  border: 2px outset #fff; padding: .2rem .6rem;\n  margin: .3rem 0; }\n.ptoggle { background: var(--card); border: 2px outset #fff;\n"
       "  padding: .2rem .8rem; }\n.ptoggle.active { border-color: var(--acc); color: var(--acc); font-weight: 600; }\n.finding { margin: .4rem 0 0; font-size: 1.05rem; }\n"
       ".finding-meta, .sub { color: var(--muted); font-weight: 400;\n  font-size: .8rem; margin: .1rem 0 .5rem; }\n#wf-readout { font-size: .82rem; min-height: 1.2em;\n"
       "  font-variant-numeric: tabular-nums; color: var(--muted); }\n.wf rect:focus-visible, button:focus-visible, .cta:focus-visible,\n.tab:focus-visible, select:focus-visible, input:focus-visible {\n"
       "  outline: 2px solid var(--acc); outline-offset: 1px; }\n@media (prefers-reduced-motion: reduce) {\n  *, *::before, *::after { animation: none !important; transition: none !important; }\n"
       "}\n/* coffe-dqz: una vista a la vez a TODO ancho (revierte deliberadamente\n   la parte desktop de FPA-152: \"en desktop las vistas van en secuencia\");\n"
       "   el ancla href de cada tab queda como fallback degradado sin JS. */\n#summary, #cost, #breakdown, #models, #gasto, #habits, #outlook, #energy,\n#data { display: none; }\n"
       "body[data-view=\"summary\"] #summary,\nbody[data-view=\"cost\"] #cost,\nbody[data-view=\"breakdown\"] #breakdown,\n"
       "body[data-view=\"models\"] #models,\nbody[data-view=\"gasto\"] #gasto,\nbody[data-view=\"habits\"] #habits,\n"
       "body[data-view=\"outlook\"] #outlook,\nbody[data-view=\"energy\"] #energy,\nbody[data-view=\"data\"] #data { display: block; }\n"
       "@media print {\n  /* coffe-dqz: la impresión incluye todas las vistas; !important vence\n     al toggle por id de la regla base */\n"
       "  .fpa-view { display: block !important; }\n}\n@media (max-width:599px) {\n"
       "  /* FPA-092: una columna (.cards-grid ya colapsa sola con auto-fit) */\n  header.site h1 { font-size: 1.1rem; }\n  /* coffe-2ni/FPA-109: primer viewport 390x844 con título + selector +\n"
       "     primera headline — ritmo vertical compacto (estructura, no tokens):\n     la línea decorativa retro sale, la topbar y los márgenes se aprietan. */\n  header.site .retro { display: none; }\n"
       "  .topbar { padding: .25rem .4rem; gap: .3rem; }\n  /* coffe-tmm.3/FPA-109: título + selector + primera headline completos en\n     el primer viewport 390x844. El topbar con tabs de texto ancho apilaba\n"
       "     3 filas (~46px c/u); tabs más compactos (misma min-height 44px táctil)\n     bajan a 2 filas, y la headline card se aprieta ~14px vía padding.\n     Estructura y tema intactos. */\n"
       "  .topbar .tab { padding: .4rem .45rem; font-size: .85rem; }\n  .headline { padding: .5rem .6rem; }\n  .headline .interp, .headline .reason { margin: .15rem 0 0; }\n"
       "  main .fpa-view > h2 { font-size: 1.2rem; margin: .3rem 0 .1rem; }\n  #summary { margin: .5rem 0; }\n  .banner-partial { padding: .3rem .6rem; }\n"
       "  .marginalia { margin: .3rem 0 .4rem; }\n  .claims { margin: .5rem 0; }\n  /* FPA-092/177: tablas anchas en contenedor con scroll horizontal y\n"
       "     primera columna fija */\n  .ttree, .btable, .fc-tbl, #data table, table.small {\n    display: block; overflow-x:auto; white-space: nowrap; }\n"
       "  .ttree th:first-child, .ttree td:first-child, .btable td:first-child,\n  .fc-tbl td:first-child, #data th:first-child, #data td:first-child,\n  table.small th:first-child, table.small td:first-child {\n"
       "    position:sticky; left: 0; background: var(--bg); }\n  /* coffe-26b: touch targets WCAG 2.5.8 — los 5 inputs del dashboard\n     suben a 44px (Apple HIG; coste nulo); los 73 summaries anidados del\n"
       "     ttree suben a >= 24px (mínimo AA real) porque llevarlos a 44px\n     duplicaría el scroll del Desglose, denso por diseño. */\n  .fc-input, .b-input { min-height: 44px; }\n"
       "  .ttree details > summary { padding: .25rem 0; }\n  /* coffe-26b: labels del forecast en bloque — inline fluyen con el\n     label anterior y parten el texto con el input intercalado. */\n"
       "  #forecast > label { display: block; margin: .45rem 0; }\n  /* coffe-26b: al cambiar de tab el h2 de la vista entra bajo la\n     topbar (sticky ~4rem) en vez de a ~350px de header común. */\n"
       "  .fpa-view > h2 { scroll-margin-top: 4rem; }\n}\n"
       ))

CSS_LEGACY = (  # retro confinado a header/footer, sin animación (FPA-178)
    ".retro::before { content: '▚▞ '; color: var(--acc); }"
)


def _finding_impl(spec):
    """Partes del hallazgo desde la tupla (valor, métrica, threshold,
    template, fallback, fmt). meta trae métrica, valor (o n/a) y
    threshold, nunca vacío."""
    value, metric_name, threshold_text, template, fallback, fmt = spec
    if value is None:  # FPA-161: fallback al label descriptivo
        title = fallback
        val = '<span class="na">n/a</span>'
    else:
        title = template.format(fmt(value))
        val = fmt(value)
    meta = (f'métrica: {esc_html(metric_name)} = {val} · '
            f'threshold: {esc_html(threshold_text)}')
    return title, meta


def _finding_spec(*args, _fmt=None):
    """Empaquetar los argumentos del hallazgo con el fmt efectivo
    (default: porcentaje con un decimal). Los fmt pueden llegar como 6º
    argumento posicional (llamadas internas) o como keyword `_fmt`."""
    if _fmt is not None:
        args = args[:5]
    elif len(args) > 5:
        _fmt = args[5]
        args = args[:5]
    return args + (_fmt or (lambda v: f"{100 * v:.1f}%"),)


def finding(*args, _fmt=None):
    """FPA-160/161: bloque título-hallazgo. Título computado de una métrica
    nombrada + threshold, ambos visibles (FPA-009); valor no disponible →
    fallback al label descriptivo."""
    title, meta = _finding_impl(_finding_spec(*args, _fmt=_fmt))
    return (f'<h2 class="finding">{esc_html(title)}</h2>'
            f'<p class="small finding-meta">{meta}</p>')


def finding_parts(*args, _fmt=None):
    """Partes del hallazgo: (título, meta) — para armar summary + meta en
    details. meta trae métrica, valor (o n/a) y threshold, nunca vacío."""
    return _finding_impl(_finding_spec(*args, _fmt=_fmt))


def top5_rows(rows_html):
    """FPA-155: primeras 5 filas visibles, el resto oculto (extra-row) con
    botón 'Mostrar todo (N)'. Devuelve (filas_html, botón_html)."""
    trs = re.findall(r"<tr.*?</tr>", rows_html, flags=re.S)
    if len(trs) <= 5:
        return rows_html, ""
    extra = "".join(t.replace("<tr", '<tr class="extra-row" hidden', 1)
                    for t in trs[5:])
    btn = (f'<button class="show-all" type="button">Mostrar todo '
           f'({len(trs) - 5})</button>')
    return "".join(trs[:5]) + extra, btn


def number_figures(html):
    """FPA-144: numeración automática de tablas (<caption>) y figuras
    (<figcaption>) — sin duplicados (verificado por FPA-106)."""
    n = [0]

    def _cap(m):
        n[0] += 1
        return f"<caption>Tabla {n[0]} — {m.group(1)}</caption>"

    html = re.sub(r"<caption>(.*?)</caption>", _cap, html, flags=re.S)
    f = [0]

    def _fig(m):
        f[0] += 1
        return f'<figcaption class="small">Figura {f[0]} — {m.group(1)}</figcaption>'

    html = re.sub(r'<figcaption class="small">(.*?)</figcaption>', _fig,
                  html, flags=re.S)
    return html


def add_export_buttons(html):
    """FPA-169: inserta botón 'Exportar CSV' delante de cada tabla y
    'Descargar SVG' delante de cada chart SVG (clase chart). El click lo
    maneja el JS por delegación; sin librerías externas."""
    html = re.sub(
        r"<table",
        '<button class="export-csv" type="button">Exportar CSV</button><table',
        html)
    html = re.sub(
        r'<svg class="(wf |enb-chart )chart"',
        '<button class="dl-svg" type="button">Descargar SVG</button>'
        '<svg class="\\1chart"',
        html)
    return html


def ctas_html(cfg):
    """FPA-165/168: CTAs del Summary desde el config — 1 primario + hasta 3
    secundarios. Los targets vacíos ya hicieron fallar validate_config
    (FPA-168); labels verb-first ≤4 palabras (FPA-172)."""
    ctas = cfg.get("ctas") or {}
    out = ['<div class="ctas" id="view-ctas">']
    p = ctas.get("primary") or {}
    if p.get("target"):
        out.append(f'<a class="cta cta-primary" '
                   f'href="{esc_html(p["target"])}">'
                   f'{esc_html(p["label"])}</a>')
    for c in (ctas.get("secondary") or [])[:3]:
        if c.get("target"):
            out.append(f'<a class="cta" href="{esc_html(c["target"])}">'
                       f'{esc_html(c["label"])}</a>')
    out.append("</div>")
    return "".join(out)
from viz_fpa_common import _link  # noqa: E402

_link(globals())
