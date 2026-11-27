"""Módulo extraído de viz-ddl.py (decomposición, comportamiento idéntico):
armado del HTML autocontenido (tabs, export, disclosure, CSS, JS), carga y
validación del reporte.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_theme  # noqa: E402
import schema_validate  # noqa: E402
from viz_ddl_common import (DDL_SCHEMA, VIEWS, VIEW_TITLES, esc)


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


def _tab_html(v):
    selected = ' aria-selected="true"' if v == VIEWS[0] else ""
    return (f'<a class="tab" role="tab" href="#{v}" data-view="{v}"'
            f'{selected}>{esc(VIEW_TITLES[v])}</a>')


def tabs_html():
    tabs = "".join(
        _tab_html(v) for v in VIEWS)
    return f'<div class="tabs" role="tablist">{tabs}</div>'


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
    from viz_ddl_views import sections_html
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
  {site_theme.back_link()}
  <h1>{esc(title)}</h1>
</header>
<main>
{DISCLOSURE}
{body}
</main>
<script>{JS % {"views": json.dumps(list(VIEWS))}}</script>
</body>
</html>"""
