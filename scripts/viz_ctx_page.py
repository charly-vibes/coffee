#!/usr/bin/env python3
"""
viz_ctx_page.py — documento HTML de viz-ctx.py (epic
add-context-dashboard, ctx-viz)

Purpose: ensamblar la página autocontenida: <head> con el estilo del
tema compartido, placeholder honesto cuando no hay datos (patrón
FPA-108) y página completa con tabs/deep-link (coffe-r3c 2.8) y export
CSV/SVG sin librerías (patrón FPA-169).

Responsibilities:
- placeholder(model): página "sin datos" con la razón visible.
- full(model, secs, scope): documento completo a partir de las secciones
  de viz_ctx_views.py — sin dependencias externas (anti-goal FPA-145).

Rationale: stdlib-only, tema compartido scripts/site_theme.py. Split de
viz-ctx.py (coffe-v5f): módulo aparte para que el archivo principal
quede bajo el límite de líneas.
"""

import html
import json

import site_theme

TABS = (("dist", "Distribución"), ("comp", "Composición"),
        ("time", "Tiempo"), ("cost", "Coste"), ("ley", "Ley"),
        ("pat", "Patologías"))

_VIEWS_JSON = json.dumps([k for k, _ in TABS])

_SCRIPT = f"""<script>
(function () {{
  var VIEWS = {_VIEWS_JSON};
  function setView(key) {{
    if (VIEWS.indexOf(key) < 0) return;
    document.body.setAttribute("data-view", key);
    document.querySelectorAll(".tab").forEach(function (t) {{
      if (t.getAttribute("data-view") === key)
        t.setAttribute("aria-current", "true");
      else t.removeAttribute("aria-current");
    }});
  }}
  function fromHash() {{
    var h = (location.hash || "").replace("#", "");
    setView(h || "dist");
  }}
  document.querySelectorAll(".tab").forEach(function (t) {{
    t.addEventListener("click", function () {{
      setView(t.getAttribute("data-view"));
    }});
  }});
  window.addEventListener("hashchange", fromHash);
  fromHash();
  // export CSV (tablas) / SVG (charts) — sin librerías (FPA-169 patrón)
  function tableToCSV(table) {{
    return Array.prototype.slice.call(table.querySelectorAll("tr"))
      .map(function (tr) {{
        return Array.prototype.slice.call(tr.querySelectorAll("th,td"))
          .map(function (c) {{
            var t = c.textContent.replace(/\\s+/g, " ").trim();
            return /[",\\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t;
          }}).join(",");
      }}).join("\\n");
  }}
  function downloadText(name, text, mime) {{
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], {{type: mime}}));
    a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
  }}
  document.querySelectorAll(".ctx-table").forEach(function (t) {{
    var b = document.createElement("button");
    b.className = "export-csv"; b.type = "button";
    b.textContent = "Exportar CSV";
    b.addEventListener("click", function () {{
      downloadText("ctx-tabla.csv", tableToCSV(t), "text/csv");
    }});
    t.parentNode.insertBefore(b, t);
  }});
  document.querySelectorAll(".ctx-hist, .scatter, .stack").forEach(function (s) {{
    var b = document.createElement("button");
    b.className = "dl-svg"; b.type = "button";
    b.textContent = "Descargar SVG";
    b.addEventListener("click", function () {{
      downloadText("ctx-chart.svg",
        '<?xml version="1.0" encoding="UTF-8"?>\\n' + s.outerHTML,
        "image/svg+xml");
    }});
    s.parentNode.insertBefore(b, s);
  }});
  window.__ctx = {{ setView: setView }};
}})();
</script>"""


def placeholder(model):
    """Página honesta cuando el reporte no trae contexto (FPA-108)."""
    t = site_theme.TOKENS
    v = site_theme.css_vars()
    base = site_theme.base_css()
    reason = html.escape(model.get("reason") or "unavailable")
    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Contexto — sin datos</title>
<style>{v}{base}
body {{ margin: 0; padding: 18px; background: {t['--bg']}; color: {t['--fg']};
        font-size: 13px; font-family: Arial, sans-serif; }}
</style></head>
<body>
{site_theme.back_link()}
<h1>Contexto de sesiones (Pi)</h1>
<p class="placeholder">sin datos — {reason}</p>
</body></html>"""


def _head_part():
    """<head> con el estilo del dashboard (tokens del tema compartido)."""
    v = site_theme.css_vars()
    base = site_theme.base_css()
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Contexto — distribución del pico por sesión (Pi)</title>
<style>
{v}{base}
  body {{ margin: 0; padding: 18px; background: var(--bg); color: var(--fg);
          font-size: 13px; font-family: Arial, sans-serif; }}
  h1 {{ font-size: 16px; margin: 0 0 4px; }}
  .scope {{ color: var(--muted); margin: 0 0 12px; }}
  .card {{ background: var(--panel); border: 1px solid var(--line);
           padding: 12px; margin-bottom: 12px; }}
  .muted {{ color: var(--muted); }}
  .ctx-hist {{ width: 100%; height: auto; }}
  .axis {{ stroke: var(--grid); }}
  .bar {{ fill: var(--accent2); }}
  .bar:hover {{ fill: var(--accent); }}
  .tick {{ font-size: 10px; fill: var(--muted); }}
  .pctl-acc {{ stroke: var(--accent); }}
  .pctl-good {{ stroke: var(--good); }}
  .pctl-acc2, .pctl-navy {{ stroke: var(--navy); }}
  .pctl-bad {{ stroke: var(--bad); }}
  .pctl.acc {{ fill: var(--accent); }}
  .pctl.good {{ fill: var(--good); }}
  .pctl.acc2, .pctl.navy {{ fill: var(--navy); }}
  .pctl.bad {{ fill: var(--bad); }}
  .tag {{ background: var(--yellow); font-size: 10px; padding: 1px 4px;
          border: 1px solid var(--line); }}
  dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; }}
  dt {{ color: var(--muted); }}  dd {{ margin: 0; }}
  .stack .seg.c1 {{ fill: var(--accent); }}
  .stack .seg.c2 {{ fill: var(--accent2); }}
  .stack .seg.c3 {{ fill: var(--good); }}
  .stack .seg.c4 {{ fill: var(--navy); }}
  .stack .seg.c5 {{ fill: var(--yellow); }}
  .stack .seg.c6 {{ fill: var(--muted); }}
  .legend .lg {{ margin-right: 10px; font-size: 11px; }}
  .legend .sw {{ display: inline-block; width: 10px; height: 10px;
                 border: 1px solid var(--line); vertical-align: -1px;
                 margin-right: 3px; }}
  .legend .sw.c1 {{ background: var(--accent); }}
  .legend .sw.c2 {{ background: var(--accent2); }}
  .legend .sw.c3 {{ background: var(--good); }}
  .legend .sw.c4 {{ background: var(--navy); }}
  .legend .sw.c5 {{ background: var(--yellow); }}
  .legend .sw.c6 {{ background: var(--muted); }}
  .ctx-table {{ border-collapse: collapse; width: 100%; }}
  .ctx-table th, .ctx-table td {{ border: 1px solid var(--line);
                                  padding: 3px 8px; text-align: left; }}
  .ctx-table th {{ background: var(--panel); color: var(--muted); }}
  /* tabs una vista a la vez a todo ancho (coffe-r3c 2.8, patrón fpa) */
  .topbar {{ display: flex; gap: 4px; flex-wrap: wrap; margin: 0 0 12px;
             border: 1px solid var(--line); background: var(--panel);
             padding: 4px; }}
  .tab {{ display: inline-block; padding: 6px 12px; min-height: 44px;
          line-height: 32px; text-decoration: none; color: var(--fg);
          border: 1px solid var(--line); background: var(--card);
          font-size: .85rem; }}
  .tab[aria-current="true"] {{ background: var(--accent); color: #fff; }}
  .ctx-view {{ display: none; }}
  body[data-view="dist"] #dist,
  body[data-view="comp"] #comp,
  body[data-view="time"] #time,
  body[data-view="cost"] #cost,
  body[data-view="ley"] #ley,
  body[data-view="pat"] #pat {{ display: block; }}
  .scatter {{ width: 340px; max-width: 100%; height: auto; }}
  .scatter .axis {{ stroke: var(--grid); }}
  .scatter .dot {{ fill: var(--accent2); }}
  .scatter .dot:hover {{ fill: var(--accent); }}
  .sc-wrap {{ margin-bottom: 10px; }}
  .export-csv, .dl-svg {{ font-size: .8rem; background: var(--card);
                          border: 1px solid var(--line); padding: 4px 8px;
                          margin: 4px 4px 8px 0; cursor: pointer; }}
</style>
</head>
"""


def _body_part(secs, scope):
    """Cuerpo: nav de tabs, seis secciones, procedencia y script."""
    counts = secs["counts"]
    nav = "".join(f'<a class="tab" role="tab" href="#{k}" '
                  f'data-view="{k}">{label}</a>'
                  for k, label in TABS)
    return f"""<body data-view="dist">
{site_theme.back_link()}
<h1>Contexto de sesiones (Pi)</h1>
<p class="scope">{scope}</p>
<nav class="topbar" role="tablist">{nav}</nav>
{secs["dist"]}
{secs["comp"]}
{secs["time"]}
{secs["cost"]}
{secs["ley"]}
{secs["pat"]}
<div class="card">
  <h2>Procedencia</h2>
  <dl>
    <dt>Set limpio</dt><dd>{counts["n_clean"]} sesiones (turns > 0, sin
      patológicas) — base de histograma y percentiles</dd>
    <dt>Excluidas</dt><dd>{counts["exc"]} patológicas (bloque assistant > umbral
      de config), visibles en la vista Patologías</dd>
    <dt>Stubs</dt><dd>{counts["stubs"]} sesiones sin turnos — nunca sumadas como 0
      (loss visible)</dd>
    <dt>Factor</dt><dd>chars_per_token {counts["cpt"]} — <span class="tag">assumed</span>,
      dirección de bias documentada</dd>
  </dl>
</div>
{_SCRIPT}
</body></html>"""


def full(secs, scope):
    """Documento completo (sin dependencias externas) con las seis
    vistas, tabs y deep-link (coffe-r3c 2.8)."""
    return _head_part() + _body_part(secs, scope)
