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
    return (f'<p class="placeholder">Vista “{VIEW_TITLES[view]}”: {msg}.'
            f'{prov_tag("missing")}</p>')


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


def sections_html(report):
    out = []
    for i, v in enumerate(VIEWS):
        hidden = "" if i == 0 else " hidden"
        out.append(f'<section class="view" id="{v}" data-view="{v}"'
                   f'{hidden}>{placeholder(v, report)}</section>')
    return "\n".join(out)


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
