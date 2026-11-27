#!/usr/bin/env python3
"""
viz_fpa_guide.py — ingesta de la guía de análisis a 4 niveles
(coffe-gen.3/gen.4, change update-fpa-site-integration).

Purpose: parsear docs/fpa-analyses-guide.md (español, 22 entradas /
21 análisis numerados ×
ELI5/Cotidiano/Practicante/Experto) con stdlib y exponerlo al sitio.

Responsibilities:
- render_guide_html(): data/fpa-guide.html autocontenida en el tema
  compartido (scripts/site_theme.py), anchors por análisis
- CLI (--check valida grounding; sin flag, genera el HTML)

Rationale: el contenido es verdad del repo (markdown versionado, pase de
grounding en coffe-gen.1); el generador nunca copia el texto a strings
propios. Las cifras del snapshot original se reemplazaron por cifras
derivadas en generación — la guía NO participa de la verificación
README↔JSON (FPA-143) y sus cifras se etiquetan como foto del reporte.
El parsing y las guardas de grounding viven en viz_fpa_guide_parse.py;
las cifras derivadas y los chips de marginalia, en
viz_fpa_guide_figures.py. Este módulo re-exporta la API pública para que
los consumidores (viz-fpa.py, viz_fpa_render.py, viz-gantt.py y los
tests) sigan importando de acá.
"""

import argparse
import html as html_mod
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_theme  # noqa: E402
from viz_fpa_guide_parse import (  # noqa: E402
    ANALYSIS_SURFACES,
    CITED_THRESHOLDS,
    CODE_DEFAULTS,
    GUIDE_MD,
    HEADING_RE,
    NIVEL_RE,
    analyses_flat,
    check_documentation,
    load_guide,
)
from viz_fpa_guide_figures import (  # noqa: E402
    FIG_TOKEN_RE,
    derive_figures,
    resolve_figures,
)

NIVEL_TEASER = "1 · ELI5"
NIVEL_COTIDIANO = "2 · Cotidiano"
NIVEL_PRACTICANTE = "3 · Practicante"

OUT = Path("data/fpa-guide.html")
CONFIG = Path("config/fpa.json")

_MARKDOWN_RE = re.compile(r"\*\*?|`")


def _plain(text):
    """Markdown básico → texto plano (para tooltip y notas)."""
    return html_mod.escape(_MARKDOWN_RE.sub("", text))


def _anchor(num):
    return f"analisis-{html_mod.escape(num)}"


def _mchip(a, figures=None):
    """Un chip de marginalia: <details> nativo con tooltip ELI5 (title),
    nivel 2 visible al expandir, nivel 3 colapsado dentro y enlace a la
    guía completa (FPA coffe-gen.4). Con `figures`, resuelve {{fig:*}}
    del reporte vigente (coffe-8kx)."""
    if figures is not None:
        a = dict(a)
        a["levels"] = {
            lvl: FIG_TOKEN_RE.sub(
                lambda m: figures.get(m.group(1), m.group(0)), text)
            for lvl, text in a["levels"].items()}
    teaser = _plain(a["levels"][NIVEL_TEASER])
    daily = _plain(a["levels"][NIVEL_COTIDIANO])
    prac = _plain(a["levels"][NIVEL_PRACTICANTE])
    anchor = _anchor(a["num"])
    return (
        f'<details class="mchip" title="{teaser}">'
        f'<summary>¿Qué es esto? · {html_mod.escape(a["num"])}</summary>'
        f'<div class="mnote">'
        f'<div class="mlevel">{daily}</div>'
        f'<details class="mchip-deep">'
        f'<summary>Cómo se computa</summary>'
        f'<div class="mlevel">{prac}</div>'
        f'</details>'
        f'<div class="mlevel"><a href="fpa-guide.html#{anchor}">'
        f'Guía completa → análisis {html_mod.escape(a["num"])}</a></div>'
        f'</div>'
        f'</details>')


def marginalia_html(guide_dict, cfg, surface, figures=None):
    """Strip de marginalia para una superficie (6 vistas + data + gantt):
    chips por análisis mapeado, con tooltip del teaser ELI5.

    coffe-8kx: con `figures` (derivadas del reporte vigente), los tokens
    {{fig:*}} del texto se resuelven también en los chips — si no, el chip
    de energía citaría 0.0 kWh en el fallback sin config (FPA-008: nunca
    un cero que parezca medición)."""
    by_num = {a["num"]: a for a in analyses_flat(guide_dict)}
    chips = []
    for num, surfaces in ANALYSIS_SURFACES:
        if surface in surfaces and num in by_num:
            chips.append(_mchip(by_num[num], figures))
    if not chips:
        return ""
    label = ('¿Qué es esto? Los análisis que esta sección mira '
             '(tooltip = versión simple; al abrir, la explicación):')
    return (f'<div class="marginalia" id="m-{html_mod.escape(surface)}">'
            f'<span class="mlabel">{html_mod.escape(label)}</span>'
            f'{"".join(chips)}</div>')


CSS = site_theme.base_css() + """
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  line-height: 1.5; font-family: var(--font-stack); }
main { max-width: 900px; margin: 0 auto; padding: 0 1rem 2rem; }
header.site, footer.site { border-bottom: 1px solid var(--line);
  max-width: 900px; margin: 0 auto; padding: .8rem 1rem; }
footer.site { border: 0; border-top: 1px solid var(--line); }
header.site h1 { margin: 0; font-size: 1.35rem; }
.meta, .small { color: var(--muted); font-size: .82rem; }
.skip { position: absolute; left: -9999px; }
.skip:focus { left: .5rem; top: .5rem; background: var(--card);
  padding: .4rem; border: 1px solid var(--line); }
h2 { font-size: 15px; color: var(--navy); margin: 26px 0 8px;
  text-transform: uppercase; letter-spacing: .06em;
  border-bottom: 3px double var(--navy); padding-bottom: 3px; }
.analysis { background: var(--card); border: 2px outset #fff;
  box-shadow: 3px 3px 0 #404040; padding: .8rem 1rem; margin: 0 0 1rem; }
.analysis h3 { margin: 0 0 .4rem; font-size: .95rem; }
.analysis h3 .fpa { font-size: .7rem; color: var(--muted); font-weight: 400; }
.level { margin: .45rem 0; padding-left: .6rem; border-left: 4px double
  var(--navy); }
.level b { color: var(--accent2); }
.volver { font-size: .78rem; }
nav.toc { background: var(--card); border: 2px outset #fff;
  padding: .7rem 1rem; margin: 1rem 0; }
nav.toc a { color: var(--navy); text-decoration: none; }
"""


def _analysis_html(a):
    """Un análisis (niveles + anchor + ids) → bloque HTML."""
    anchor = _anchor(a["num"])
    levels = "".join(
        f'<div class="level"><b>{html_mod.escape(k)}</b> '
        f'{html_mod.escape(v)}</div>'
        for k, v in a["levels"].items())
    ids_text = html_mod.escape(a.get("ids_text", ""))
    return (
        f'<div class="analysis" id="{anchor}">'
        f'<h3>{html_mod.escape(a["num"])}. '
        f'{html_mod.escape(a["title"])} '
        f'<span class="fpa">{ids_text}</span></h3>'
        f'{levels}'
        f'<p class="small volver"><a href="#toc">↑ índice</a></p>'
        f'</div>')


def _section_toc_html(s):
    """Fila del índice: clave, título y anchors de sus análisis."""
    items = "".join(
        f'<a href="#{_anchor(a["num"])}">{a["num"]}</a>'
        for a in s["analyses"])
    return (f'<div><b>{s["key"]}.</b> {html_mod.escape(s["title"])} '
            f'{items}</div>')


def render_guide_html(guide_dict, cfg, generated=None):
    """HTML autocontenido de la guía completa (determinista)."""
    generated = generated or datetime.now().strftime("%Y-%m-%d %H:%M")
    site_name = cfg.get("site_name", "Uso y costos de IA")
    n_analyses = len(analyses_flat(guide_dict))  # coffe-udt: 10a/10b → 20
    toc = "".join(_section_toc_html(s) for s in guide_dict["sections"])
    sections_html = "".join(
        _analysis_html(a)
        for s in guide_dict["sections"] for a in s["analyses"])
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Ctext y='13' font-size='13'%3E%E2%98%95%3C/text%3E%3C/svg%3E">
<title>Guía de análisis — {html_mod.escape(site_name)}</title>
<style>{CSS}</style>
</head>
<body>
<a class="skip" href="#toc">Saltar al índice</a>
<header class="site">
  {site_theme.back_link()}
  <h1>Guía de análisis — {html_mod.escape(site_name)}</h1>
  <p class="meta">Generado: {generated} · fuente: docs/fpa-analyses-guide.md
  ({n_analyses} análisis × 4 niveles)</p>
</header>
<main>
<nav class="toc" id="toc">
  {toc}
</nav>
{sections_html}
<footer class="site">
  <p class="small">Guía de análisis · generado por viz_fpa_guide.py
  (stdlib-only) · las cifras citadas provienen del reporte vigente y son una
  foto del momento de la traducción — el dashboard calcula las cifras vivas
  en cada corrida.</p>
</footer>
</main>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(
        description="Guía de análisis a 4 niveles (viz_fpa_guide.py)")
    ap.add_argument("--check", action="store_true",
                    help="solo validar grounding (mapeo + umbrales vs config)")
    ap.add_argument("--report", default=str(Path("data/usage_report_v3.json")))
    args = ap.parse_args()
    text = GUIDE_MD.read_text(encoding="utf-8")
    cfg = json.loads(CONFIG.read_text())
    errors = check_documentation(cfg, text)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    # coffe-udt/0zp: los tokens {{fig:...}} se resuelven contra el reporte
    # vigente acá y en --check (falla loud si un token no existe).
    report = json.loads(Path(args.report).read_text())
    text = resolve_figures(text, report, cfg)
    if args.check:
        print("OK: guía con grounding verificado (mapeo + umbrales + cifras "
              "derivadas del reporte vigente)")
        return
    guide_dict = load_guide(text)
    OUT.write_text(render_guide_html(guide_dict, cfg), encoding="utf-8")
    print(f"OK → {OUT} ({len(analyses_flat(guide_dict))} análisis, cifras "
          f"derivadas del reporte)")


if __name__ == "__main__":
    main()
