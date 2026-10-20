#!/usr/bin/env python3
"""Genera index.html: entrada del sitio publicado en GitHub Pages.

A diferencia del index.html artesanal anterior (cuyas cifras del marquee
quedaban viejas en cada refresh), este generador deriva TODO de
data/usage_report_v3.json + data/charges.json — mismos insumos que los
dashboards — así el índice nunca queda desactualizado (bd coffe-85z).

Estética 90s-corporate deliberada (ver snap 2026-09-19); el tema vive en
scripts/site_theme.py (coffe-gen.2) y el label del dashboard sale del
site_name del config (sin jerga, coffe-gen.2/gen.3); el contenido es
una lista corta: el dashboard + su guía de análisis + el Gantt + los
dashboards de contexto y efecto DDL + el reporte JSON crudo. Los JSON
históricos/auxiliares (v2 sin filtrar, tool_timeline) NO se enlazan.
El guard de cobertura vive en tests/test_site_nav.py: todo data/*.html
debe estar enlazado desde acá (sin dashboards huérfanos).

Sin dependencias. Determinista: ninguna fecha viene del reloj; la fecha
de actualización sale de metadata.date_range.end del reporte.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "data" / "usage_report_v3.json"
CHARGES = ROOT / "data" / "charges.json"
CONFIG = ROOT / "config" / "fpa.json"
OUT = ROOT / "index.html"

sys.path.insert(0, str(ROOT / "scripts"))
import site_theme  # noqa: E402

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def load_json(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_stats(report, charges):
    """Deriva las cifras del índice desde el reporte (función pura)."""
    meta = report["metadata"]
    end = meta["date_range"]["end"]
    y, m, _ = end.split("-")
    n_proveedores = len(charges["providers"]) if charges else 0
    return {
        "interacciones": meta["total_interactions"],
        "proyectos": meta["total_projects"],
        "actualizado": end,
        "edicion": f"{MESES[int(m) - 1].capitalize()} {y}",
        "proveedores": n_proveedores,
    }


TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex, nofollow">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Ctext y='13' font-size='13'%3E%E2%98%95%3C/text%3E%3C/svg%3E">
<title>coffee — tracking de uso de IA</title>
<style>
@@THEME_BASE@@
  body { margin: 0; padding: 18px; background: var(--bg); color: var(--fg);
         font-size: 13px; line-height: 1.45; font-family: var(--font-stack); }
  .banner { background: var(--navy); color: #fff; padding: 10px 14px;
            border: 2px outset #fff; margin-bottom: 10px; }
  .banner h1 { font-size: 22px; margin: 0; letter-spacing: 1px; }
  .banner .tag { color: var(--bg); font-size: 11px; font-style: italic; }
  marquee { background: var(--yellow); color: #000; border: 2px inset #fff;
            font-weight: bold; font-size: 12px; padding: 3px 0; margin-bottom: 16px; }
  .frame { background: var(--panel); border: 2px outset #fff; padding: 18px; max-width: 680px; margin: 0 auto; }
  blockquote.epigraph { margin: 0 0 18px; padding: 10px 14px; border-left: 4px double var(--navy);
    color: var(--muted); font-style: italic; font-size: 12px; }
  blockquote.epigraph .who { display: block; margin-top: 6px; font-style: normal; }
  table.dir { width: 100%; border-collapse: collapse; }
  table.dir th { background: var(--navy); color: #fff; text-align: left; padding: 5px 10px; border: 1px solid #404040; }
  table.dir td { padding: 6px 10px; border: 1px solid var(--grid); }
  table.dir tr:hover td { background: var(--yellow); }
  a { color: var(--navy); font-weight: bold; }
  a:visited { color: #800080; }
  a:hover { color: var(--accent); }
  .desc { color: var(--muted); font-size: 11px; font-weight: normal; }
  footer { max-width: 680px; margin: 18px auto 0; font-size: 11px; color: var(--muted); text-align: center; }
</style>
</head>
<body>
<div class="banner">
  <h1>☕ coffee — tracking de uso de IA</h1>
  <div class="tag">Charly Vibes Analytics S.A. · división de métricas y Productivity Enhancement</div>
</div>
<marquee scrollamount="4">★★★ BIENVENIDO A NUESTRO SITIO ★★★ datos actualizados al @@ACTUALIZADO@@ ★★★ @@INTERACCIONES@@ interacciones · @@PROYECTOS@@ proyectos · @@PROVEEDORES@@ proveedores ★★★ esta página se ve mejor en Netscape Navigator 4.0 a 800×600 ★★★ firma nuestro guestbook ★★★</marquee>

<div class="frame">
  <blockquote class="epigraph">
    Do you feel like a chain store<br>
    Practically floored<br>
    One of many zeroes<br>
    Kicked around bored
    <span class="who">— Blur, "Coffee &amp; TV"</span>
  </blockquote>

  <table class="dir">
    <tr><th colspan="2">ÍNDICE DE REPORTES — EDICIÓN @@EDICION@@</th></tr>
    <tr>
      <td><a href="data/fpa-dashboard.html">@@SITE_NAME@@</a><br>
          <span class="desc">Dashboard principal: presupuestos, bridge precio-volumen-mix, forecast, alertas, cash real del ledger y economía de suscripción</span></td>
    </tr>
    <tr>
      <td><a href="data/fpa-guide.html">Guía de análisis</a><br>
          <span class="desc">Cada análisis del dashboard explicado a 4 niveles: ELI5, cotidiano, practicante, experto</span></td>
    </tr>
    <tr>
      <td><a href="data/gantt-multitasking.html">Gantt de multitasking</a><br>
          <span class="desc">Actividad proyecto × día con concurrencia diaria</span></td>
    </tr>
    <tr>
      <td><a href="data/context-dashboard.html">Contexto de sesiones</a><br>
          <span class="desc">Pico de contexto por sesión: histograma, percentiles, composición, costos, patologías</span></td>
    </tr>
    <tr>
      <td><a href="data/ddl-dashboard.html">Efecto del toolset DDL</a><br>
          <span class="desc">Adopción, dosis-respuesta, pre/post con DiD e ITS, lazo de feedback y calidad</span></td>
    </tr>
    <tr>
      <td><a href="data/usage_report_v3.json">usage_report_v3.json</a><br>
          <span class="desc">Los datos crudos del reporte, por si querés correr tu propio análisis</span></td>
    </tr>
  </table>
</div>

<footer>
<hr noshade size="2">
© 1996–2026 Charly Vibes Analytics S.A. · <i>Best viewed in Netscape Navigator 4.0 at 800×600</i><br>
🚧 UNDER CONSTRUCTION 🚧 · generado con <code>scripts/viz-index.py</code> · noindex
</footer>
</body>
</html>
"""


def render(stats, site_name):
    html = (TEMPLATE
            .replace("@@THEME_BASE@@", site_theme.base_css())
            .replace("@@SITE_NAME@@", site_name)
            .replace("@@ACTUALIZADO@@", stats["actualizado"])
            .replace("@@INTERACCIONES@@", f"{stats['interacciones']:,}")
            .replace("@@PROYECTOS@@", str(stats["proyectos"]))
            .replace("@@PROVEEDORES@@", str(stats["proveedores"]))
            .replace("@@EDICION@@", stats["edicion"]))
    return html


def main():
    report = load_json(REPORT)
    if report is None:
        sys.exit(f"ERROR: falta {REPORT}; corré antes scripts/usage-tracker.py")
    charges = load_json(CHARGES)
    if charges is None:
        print(f"WARNING: falta {CHARGES}; el índice sale sin proveedores", file=sys.stderr)
    stats = build_stats(report, charges)
    cfg = load_json(CONFIG) or {}
    site_name = cfg.get("site_name", "Uso y costos de IA")
    OUT.write_text(render(stats, site_name), encoding="utf-8")
    print(f"OK → {OUT.relative_to(ROOT)} "
          f"({stats['interacciones']:,} interacciones, actualizado al {stats['actualizado']})")


if __name__ == "__main__":
    main()
