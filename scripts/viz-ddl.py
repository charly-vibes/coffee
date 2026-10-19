#!/usr/bin/env python3
"""
viz-ddl.py — dashboard del análisis ddl-effect (add-ddl-effectiveness-analysis).

Lee `data/ddl_effect.json` (schema: `specs/ddl-effect.schema.json`) y genera
`data/ddl-dashboard.html`: único HTML autocontenido, SVG inline, sin recursos
externos, tema compartido (`scripts/site_theme.py`), 6 vistas con deep-link
por hash, export CSV/SVG y `--validate` (coffe-31l esqueleto; las vistas se
completan en coffe-het/470/46h, la banda de disclosure en coffe-8yf, el smoke
Chromium en coffe-x1s).

Stdlib-only, igual que el resto del repo.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema_validate  # noqa: E402
import site_theme  # noqa: E402

DDL_SCHEMA = (Path(__file__).resolve().parent.parent / "specs"
              / "ddl-effect.schema.json")

# deep-link por hash (spec: '6 vistas ... deep-link por hash'); la default es
# la primera — hash inválido ignorado, nunca 404 blanco (patrón FPA-171).
VIEWS = ("adopcion", "dosis-respuesta", "pre-post", "lazo-feedback",
         "errores", "calidad")

VIEW_TITLES = {
    "adopcion": "Línea de adopción",
    "dosis-respuesta": "Dosis-respuesta",
    "pre-post": "Pre/post + DiD",
    "lazo-feedback": "Lazo de feedback",
    "errores": "Errores",
    "calidad": "Calidad",
}

# Reporte vacío (forma del schema v1): lo usa build_html cuando el análisis
# aún no tiene datos y es la base del esqueleto del tracker.
SKELETON_REPORT = {
    "metadata": {"generated_at": None, "seed_bootstrap": 42,
                 "managed_tools": [],
                 "fleet_definition": "repos charly-vibes con actividad en "
                                     "sesiones in-scope"},
    "repos": [],
    "monthly": [],
    "analysis": {"its": [], "did": [], "dose_response": []},
}


def esc(s):
    return html.escape(str(s), quote=True)


def prov_tag(tag):
    """Tag visible de procedencia (reported/derived/cached/missing)."""
    return (f'<span class="prov-tag" title="procedencia: {esc(tag)}">'
            f'{esc(tag)}</span>')


def fig(value_html, tag):
    """Envoltorio de cifra con provenance (patrón FPA-003)."""
    return f'<span class="fig" data-provenance="{esc(tag)}">{value_html}</span>'


def placeholder(view, report):
    """Vista sin implementar todavía (esqueleto coffe-31l)."""
    tiene_datos = bool(report.get("monthly") or report.get("repos"))
    msg = ("datos cargados — la vista llega en un ticket posterior"
           if tiene_datos else "sin datos")
    return (f'<p class="placeholder">Vista “{VIEW_TITLES[view]}": {msg}.'
            f'{prov_tag("missing")}</p>')


def _fmt(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def _estimator_table(headers, rows_html, empty_msg):
    if not rows_html:
        return f'<p class="placeholder">{esc(empty_msg)}</p>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join(rows_html)
    return (f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody>'
            f'</table>')


def its_rows_html(its):
    """Filas ITS (coffe-ktu): delta con IC bootstrap y n visibles; omitidos
    con reason explícito — nunca delta 0 disfrazado (spec)."""
    rows = []
    for e in its:
        label = f"{esc(e.get('repo', '?'))} · {esc(e.get('metric', '?'))}"
        if e.get("delta") is None:
            rows.append(
                f"<tr><td>{label}</td>"
                f"<td colspan='3'>omitido: {esc(e.get('reason', 'sin razón'))}</td>"
                f"</tr>")
            continue
        rows.append(
            f"<tr><td>{label}</td>"
            f"<td>{fig(_fmt(e['delta']), 'derived')}</td>"
            f"<td>IC 95%: {_fmt(e.get('ci_low'))} … {_fmt(e.get('ci_high'))}</td>"
            f"<td>n_pre {e.get('n_pre', '?')} · n_post {e.get('n_post', '?')}</td>"
            f"</tr>")
    return rows


def did_rows_html(did_rows):
    """Filas DiD (coffe-470): punto + IC bootstrap, n de cada celda visible;
    regla dura n<3 → omitido con reason, el punto no se grafica (anti-anécdota)."""
    rows = []
    for e in did_rows:
        label = esc(e.get("metric", "?"))
        if e.get("did") is None:
            rows.append(
                f"<tr><td>{label}</td>"
                f"<td colspan='3'>omitido: {esc(e.get('reason', 'sin razón'))}</td>"
                f"</tr>")
            continue
        rows.append(
            f"<tr><td>{label}</td>"
            f"<td>{fig(_fmt(e['did']), 'derived')}</td>"
            f"<td>IC 95%: {_fmt(e.get('ci_low'))} … {_fmt(e.get('ci_high'))}</td>"
            f"<td>Δ trat {_fmt(e.get('delta_treatment'))} · "
            f"Δ ctrl {_fmt(e.get('delta_control'))}</td>"
            f"<td>n_treatment: {e.get('n_treatment', '?')} · "
            f"n_control: {e.get('n_control', '?')}</td>"
            f"</tr>")
    return rows


def view_adopcion(report):
    """Vista línea de adopción (coffe-het 5.2): timeline por repo, fechas
    null visibles como 'sin señal' (patrón FPA-008) — nunca fecha imputada."""
    repos = report.get("repos") or []
    if not repos:
        return (f'<h2>{esc(VIEW_TITLES["adopcion"])}</h2>'
                f'<p class="placeholder">Adopción: sin datos</p>')
    ordenados = sorted(
        repos,
        key=lambda r: (r.get("adoption", {}).get("first_ddl_commit") is None,
                       r.get("adoption", {}).get("first_ddl_commit") or ""))
    rows = []
    for r in ordenados:
        a = r.get("adoption", {})
        fecha = a.get("first_ddl_commit")
        badge = "" 
        if r.get("is_outlier"):
            badge = ' <span class="badge">outlier</span>'
        elif r.get("is_control"):
            badge = ' <span class="badge">control</span>'
        dose = a.get("dose")
        dose_cell = (fig(_fmt(dose), "reported") if dose is not None
                     else fig("sin señal", "missing"))
        first = (esc(fecha) if fecha
                 else fig("sin señal", "missing"))
        rows.append(
            f'<tr data-repo="{esc(r.get("repo", "?"))}">'
            f'<td>{esc(r.get("repo", "?"))}{badge}</td>'
            f"<td>{first}</td>"
            f"<td>{dose_cell}</td></tr>")
    return (f'<h2>{esc(VIEW_TITLES["adopcion"])}</h2>'
            "<p>Primer commit de <code>.ddl/</code> por repo (fechas null "
            "visibles — sin imputación):</p>"
            f'<table><thead><tr><th>repo</th><th>primer commit .ddl/</th>'
            f'<th>dosis</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')


def _scatter_svg(points):
    """Scatter dosis × outcome en SVG inline, sin librerías. Ejes fijos:
    dosis 0–1, outcome auto-escala al rango de los puntos."""
    W, H, PAD = 320, 180, 24
    outcomes = [p["outcome"] for p in points]
    lo, hi = min(outcomes), max(outcomes)
    span = (hi - lo) or 1.0
    circles = []
    for p in points:
        x = PAD + p["dose"] * (W - 2 * PAD)
        y = H - PAD - (p["outcome"] - lo) / span * (H - 2 * PAD)
        circles.append(f'<circle class="scatter-pt" cx="{x:.1f}" '
                       f'cy="{y:.1f}" r="4" '
                       f'title="{esc(p["repo"])}"/>')
    return (f'<svg class="ddl-chart" width="{W}" height="{H}" '
            f'role="img" aria-label="scatter dosis-respuesta">'
            f'{"".join(circles)}</svg>')


def view_dosis_respuesta(report):
    """Vista dosis-respuesta (coffe-het 5.3): scatter con n por punto;
    regla dura — celdas con n < 3 o sin rho no se grafican, quedan marcadas
    (anti-anécdota, spec)."""
    entries = (report.get("analysis") or {}).get("dose_response") or []
    blocks = []
    for e in entries:
        label = esc(e.get("metric", "?"))
        rho = e.get("spearman_rho")
        if rho is None or (e.get("n") or 0) < 3:
            reason = esc(e.get("reason", "n_insuficiente"))
            blocks.append(
                f'<p class="placeholder">{label}: no graficada ({reason}), '
                f'n = {e.get("n", "?")}</p>')
            continue
        points = [p for p in (e.get("points") or [])
                  if p.get("dose") is not None
                  and p.get("outcome") is not None]
        blocks.append(
            f'<div class="scatter-block"><h3>{label}</h3>'
            f"{fig(f'ρ = {rho:.2f} · n = {e["n"]}', 'reported')}"
            f"{_scatter_svg(points)}</div>")
    if not blocks:
        blocks = ['<p class="placeholder">Dosis-respuesta: sin datos</p>']
    return (f'<h2>{esc(VIEW_TITLES["dosis-respuesta"])}</h2>'
            + "\n".join(blocks))


def view_pre_post(report):
    """Vista pre/post + DiD (coffe-470): ITS por repo (coffe-ktu) arriba,
    DiD por métrica con IC bootstrap abajo. Provenance derived en toda cifra."""
    analysis = report.get("analysis") or {}
    its = _estimator_table(
        ["repo · métrica", "delta", "IC bootstrap", "n"],
        its_rows_html(analysis.get("its") or []),
        "ITS: sin datos")
    did = _estimator_table(
        ["métrica", "DiD", "IC bootstrap", "deltas", "n"],
        did_rows_html(analysis.get("did") or []),
        "DiD: sin datos")
    return (f'<h2>{esc(VIEW_TITLES["pre-post"])}</h2>'
            f"<p>Serie interrumpida por repo (media móvil trimestral, "
            f"IC bootstrap seed fija):</p>{its}"
            f"<p>Diferencia-en-diferencias 2×2 contra control "
            f"(IC bootstrap sobre deltas por repo):</p>{did}")


def add_export_buttons(html_doc):
    """Inserta botón 'Exportar CSV' delante de cada tabla y 'Descargar SVG'
    delante de cada chart (clase chart). El click lo maneja el JS por
    delegación; sin librerías externas (patrón FPA-169)."""
    html_doc = re.sub(
        r"<table",
        '<button class="export-csv" type="button">Exportar CSV</button><table',
        html_doc)
    html_doc = re.sub(
        r'<svg class="(wf |enb-chart |ddl-)?chart"',
        '<button class="dl-svg" type="button">Descargar SVG</button>'
        '<svg class="\\1chart"',
        html_doc)
    return html_doc


def load_report(path):
    """Carga el reporte; fail loud si falta o no parsea."""
    p = Path(path)
    if not p.exists():
        raise SystemExit(
            f"viz-ddl: no existe {p} — corré scripts/ddl-effect-tracker.py "
            "primero (el dashboard es derivado, nunca fuente)")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"viz-ddl: {p} no es JSON válido: {e}")


def validate_report(report):
    """Valida contra specs/ddl-effect.schema.json (única fuente: la del
    tracker); lista de errores — vacía = válido."""
    schema = json.loads(DDL_SCHEMA.read_text())
    return schema_validate.validate_against_schema(report, schema)


def tabs_html():
    tabs = "".join(
        _tab_html(v) for v in VIEWS)
    return f'<div class="tabs" role="tablist">{tabs}</div>'


def _tab_html(v):
    selected = ' aria-selected="true"' if v == VIEWS[0] else ""
    return (f'<a class="tab" role="tab" href="#{v}" data-view="{v}"'
            f'{selected}>{esc(VIEW_TITLES[v])}</a>')


def view_html(view, report):
    """Dispatcher de vistas: las implementadas renderizan su contenido, el
    resto cae al placeholder del esqueleto (los tickets het/46h/8yf van
    enchufando las suyas acá)."""
    if view == "pre-post":
        return view_pre_post(report)
    if view == "adopcion":
        return view_adopcion(report)
    if view == "dosis-respuesta":
        return view_dosis_respuesta(report)
    return placeholder(view, report)


def sections_html(report):
    out = []
    for i, v in enumerate(VIEWS):
        hidden = "" if i == 0 else " hidden"
        out.append(f'<section class="view" id="{v}" data-view="{v}"'
                   f'{hidden}>{view_html(v, report)}</section>')
    return "\n".join(out)


DISCLOSURE = (
    '<aside class="disclosure" role="note">'
    '<p><strong>Qué NO dice este dashboard:</strong> el diseño es '
    '<em>asociativo, no causal</em>. Los confusores medidos declarados son la '
    'madurez del repo (edad, commits totales) y la mezcla de modelos del '
    'período. La adopción de ddl correlaciona con la <strong>atención del '
    'mantenedor</strong> — confusor no controlable: los repos con ddl son '
    'repos que Sasha cuida más, el efecto medido incluye ese cuidado.'
    '</p></aside>')


CSS_EXTRA = """
body { max-width: 960px; margin: 0 auto; padding: 0 1rem; }
.tabs { display: flex; flex-wrap: wrap; gap: .25rem; margin: 1rem 0; }
.tab { padding: .4rem .8rem; border: 1px solid var(--border, #ccc);
       text-decoration: none; }
.tab[aria-selected="true"] { font-weight: 700; }
.view[hidden] { display: none; }
.placeholder { padding: 2rem 1rem; border: 1px dashed var(--border, #ccc); }
.prov-tag { font-size: .7rem; border: 1px solid currentColor;
            padding: 0 .3rem; margin-left: .3rem; }
.export-csv, .dl-svg { margin: .5rem 0; }
.disclosure { border: 3px double var(--bad, #800000); background: var(--card,
              #fff); padding: .8rem 1rem; margin: 1rem 0; }
"""

JS = """
(function () {
  'use strict';
  var VIEWS = %(views)s;
  function setView(v) {
    VIEWS.forEach(function (name) {
      var tab = document.querySelector('.tab[data-view="' + name + '"]');
      var sec = document.querySelector('.view[data-view="' + name + '"]');
      var on = name === v;
      if (tab) {
        if (on) tab.setAttribute('aria-selected', 'true');
        else tab.removeAttribute('aria-selected');
      }
      if (sec) {
        if (on) sec.removeAttribute('hidden');
        else sec.setAttribute('hidden', '');
      }
    });
  }
  function viewFromHash() {
    var h = (location.hash || '').slice(1);
    return VIEWS.indexOf(h) >= 0 ? h : VIEWS[0];  // inválidos ignorados
  }
  setView(viewFromHash());
  window.addEventListener('hashchange', function () {
    setView(viewFromHash());
  });

  // Export CSV/SVG por delegación (sin tablas/charts todavía: listo para
  // coffe-het/470/46h — mismo contrato que FPA-169)
  function downloadText(name, text, mime) {
    var a = document.createElement('a');
    a.href = 'data:' + mime + ';charset=utf-8,' + encodeURIComponent(text);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  function tableToCSV(t) {
    return Array.prototype.map.call(t.rows, function (row) {
      return Array.prototype.map.call(row.cells, function (c) {
        return '"' + c.textContent.replace(/"/g, '""') + '"';
      }).join(',');
    }).join('\\n');
  }
  function svgXml(s) {
    var x = new XMLSerializer().serializeToString(s);
    return '<?xml version="1.0" standalone="no"?>\\n' + x;
  }
  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest ? ev.target.closest('button') : null;
    if (!btn) return;
    var view = (viewFromHash() || 'ddl').replace(/[^a-z0-9-]/g, '');
    if (btn.classList.contains('export-csv')) {
      var t = btn.nextElementSibling;
      if (t) downloadText('ddl-' + view + '.csv', tableToCSV(t), 'text/csv');
    } else if (btn.classList.contains('dl-svg')) {
      var s = btn.nextElementSibling;
      if (s) downloadText('ddl-' + view + '.svg', svgXml(s), 'image/svg+xml');
    }
  });
})();
"""


def build_html(report):
    """HTML autocontenido: tema compartido, tabs, 6 vistas, export JS."""
    title = "Efecto del toolset ddl"
    body = (tabs_html() + "\n" + sections_html(report))
    body = add_export_buttons(body)
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{site_theme.base_css()}{CSS_EXTRA}</style>
</head>
<body>
<header>
  <h1>{esc(title)}</h1>
</header>
<main>
{DISCLOSURE}
{body}
</main>
<script>{JS % {"views": json.dumps(list(VIEWS))}}</script>
</body>
</html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", default="data/ddl_effect.json")
    ap.add_argument("--output", default="data/ddl-dashboard.html")
    ap.add_argument("--validate", metavar="ARCHIVO", default=None,
                    help="valida el JSON contra specs/ddl-effect.schema.json "
                         "y sale (0/1) sin generar HTML")
    args = ap.parse_args(argv)

    if args.validate:
        errors = validate_report(load_report(args.validate))
        if errors:
            print("viz-ddl: schema inválido:", file=sys.stderr)
            for e in errors:
                print(f"  {e}", file=sys.stderr)
            return 1
        print(f"viz-ddl: {args.validate} válido contra {DDL_SCHEMA.name}")
        return 0

    report = load_report(args.report)
    errors = validate_report(report)
    if errors:  # fail loud: el dashboard no renderiza datos inválidos
        ap.error(f"{args.report} inválido contra {DDL_SCHEMA.name}: "
                 + "; ".join(errors))

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(report))
    print(f"viz-ddl: {out} generado ({len(VIEWS)} vistas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
