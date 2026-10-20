#!/usr/bin/env python3
"""
site_theme.py — tokens del tema compartido del sitio (Corporate
Infographics™ 1996).

Purpose: única fuente de definición de la identidad visual del sitio
(tema "90s corporate" extraído de viz-dashboard.py, coffe-gen.2 / change
update-fpa-site-integration).

Responsibilities:
- exponer TOKENS: las variables CSS canónicas (--bg silver, --accent granate,
  --accent2/--navy navy, --good teal, --yellow, más los alias semánticos que
  consumen viz-fpa.py: --card, --line, --acc, --ok, --bad)
- emitir el bloque :root (css_vars()) y las reglas base compartidas
  (base_css(): box-sizing, tipografía Arial del sitio) para que cada
  generador (viz-fpa.py, viz-index.py, viz-gantt.py) las interpolate en su
  <style> en lugar de duplicar definiciones divergentes
- emitir DISCLOSURE_CSS (coffe-x6l F5): estructura/disclosure de las
  secciones <details class=tree> — summary como group-box del tema con el
  marcador ▸/▾ integrado vía ::before (nunca nodo de texto: el marcador
  nativo se suprime con list-style:none) y target táctil >=44px. Solo
  estructura: ningún token de color/tipografía nuevo.

Rationale: copiar el CSS a mano garantiza divergencia futura entre los
dashboards; un módulo stdlib es el patrón del repo (fpa_config.py). Un solo
modo (sin dark mode, decisión D2 del change) y contraste AA verificado por
tests/test_fpa_theme.py.
"""

TOKENS = {
    # === Corporate Infographics™ 1996 (extraído de viz-dashboard.py) ===
    "--bg": "#c0c0c0", "--panel": "#ffffff", "--fg": "#000000",
    "--muted": "#404040", "--accent": "#800000", "--accent2": "#000080",
    "--good": "#008080", "--grid": "#c0c0c0", "--navy": "#000080",
    "--yellow": "#ffff00",
    # Alias semánticos (nombres previos de viz-fpa.py) mapeados al tema:
    # panel blanco, borde gris, acento granate, ok teal, bad granate.
    "--card": "#ffffff", "--line": "#c0c0c0", "--acc": "#800000",
    "--ok": "#008080", "--bad": "#800000",
    # Tipografía del sitio como token para que ningún generador la duplique.
    "--font-stack": "Arial, Helvetica, 'MS Sans Serif', sans-serif",
}

FONT_STACK = ('Arial, Helvetica, "MS Sans Serif", sans-serif')

# Tokens de estructura (coffe-efw/D2): geometría del layout, no identidad —
# ningún token de color/tipografía nuevo. Los consumen los generadores que
# interpolan esta constante en su <style> (hoy: viz-fpa.py).
# - Contenedor: 960px es decisión explícita de D2 (sin widar).
# - .cards-grid: grilla DEDICADA de cards — es el único contenedor-grid;
#   marginalia, headings y CTAs nunca son sus hijos (root cause del layout
#   roto: eran grid items de #summary, que es display:grid).
# - Anatomía de card: todo hijo directo de .cards-grid es una card
#   (superficie 90s corporate: panel blanco + borde outset + sombra dura).
# - Fila de CTAs: compacta, una sola línea a >=600px, alto <=64px.
STRUCTURE_CSS = (
    'main { max-width: 960px; margin: 0 auto; padding: 0 1rem 2rem; }\n'
    '.cards-grid { display: grid; grid-template-columns: '
    'repeat(auto-fit, minmax(240px, 1fr)); gap: .7rem; margin: .6rem 0 1rem; }\n'
    '.cards-grid > * { min-width: 0; background: var(--card); '
    'border: 2px outset #fff; box-shadow: 3px 3px 0 #404040; '
    'padding: .7rem .8rem; }\n'
    '.ctas { display: flex; flex-wrap: wrap; gap: .4rem; '
    'margin: .8rem 0 0; }\n'
    '.ctas .cta { font-size: .82rem; padding: .35rem .6rem; '
    'min-height: 44px; max-height: 64px; white-space: nowrap; }\n'
)

# Disclosure de secciones (coffe-x6l F5/D5): summary como group-box 90s
# con marcador integrado. Selectores acotados a details.tree > summary
# (secciones de nivel de vista); los expanders internos (.ttree, tlabel,
# mchip) conservan sus reglas propias. Estructura compartible: sin
# colores/tipografía fuera de los tokens.
DISCLOSURE_CSS = (
    'details.tree { margin: .6rem 0; }\n'
    'details.tree > summary { display: flex; align-items: center; '
    'min-height: 44px; cursor: pointer; list-style: none; '
    'background: var(--card); border: 2px outset #fff; '
    'padding: .2rem .6rem; }\n'
    'details.tree > summary::-webkit-details-marker { display: none; }\n'
    'details.tree > summary::before { content: "\u25b8"; '
    'color: var(--muted); flex: none; padding-right: .35em; }\n'
    'details.tree[open] > summary { border-style: inset; }\n'
    'details.tree[open] > summary::before { content: "\u25be"; }\n'
    'details.tree > summary:hover { color: var(--acc); }\n'
    'details.tree > summary > h2, details.tree > summary > h3 '
    '{ margin: 0; font-size: 1rem; }\n'
)

# Marginalia progressive disclosure (coffe-gen.4): chips expandibles
# compartidos por el dashboard y el Gantt. El tooltip nativo (title)
# lleva el teaser ELI5; la expansión es <details> nativo (funciona por
# click, tap y teclado sin JS).
MARGINALIA_CSS = (
    '.marginalia { display: flex; flex-wrap: wrap; gap: .4rem; '
    'align-items: flex-start; margin: .3rem 0 .8rem; }\n'
    '.marginalia > .mlabel { color: var(--muted); font-size: .78rem; '
    'align-self: center; }\n'
    '.marginalia details.mchip { min-height: 44px; }\n'
    '.marginalia details.mchip > summary { display: inline-flex; '
    'align-items: center; min-height: 44px; cursor: pointer; '
    'background: var(--card); border: 2px outset #fff; '
    'padding: .2rem .6rem; font-size: .8rem; }\n'
    '.marginalia details.mchip > summary:hover { color: var(--accent); }\n'
    '.marginalia details.mchip[open] > summary { border-style: inset; }\n'
    '.marginalia .mnote { background: var(--panel); '
    'border: 1px solid #404040; box-shadow: 3px 3px 0 #404040; '
    'padding: .6rem .8rem; font-size: .82rem; max-width: 640px; }\n'
    '.marginalia .mnote .mlevel { margin: .3rem 0; }\n'
    '.marginalia .mnote details.mchip-deep > summary { cursor: pointer; '
    'color: var(--navy); font-size: .78rem; }\n'
    '.marginalia .mnote a { color: var(--navy); }\n'
) 


# Navegación cruzada del sitio: back-link "← Volver al índice" en toda
# página generada (los dashboards viven en data/ y el índice en la raíz,
# por eso el href relativo ../index.html — jamás data/data: coffe-ulw).
BACK_LINK_CSS = (
    # position:absolute sin ancestro posicionado → ancla al origen del
    # documento (esquina superior derecha), SIN desplazar el layout: el
    # smoke 390×844 del primer viewport (FPA-109) no tolera filas extra.
    'a.back-index { position: absolute; top: .35rem; right: .4rem; '
    'z-index: 10; display: inline-flex; align-items: center; '
    'min-height: 44px; padding: .3rem .55rem; font-size: .78rem; '
    'font-weight: bold; color: var(--navy); background: var(--card); '
    'border: 2px outset #fff; text-decoration: none; }\n'
    'a.back-index:hover { color: var(--accent); '
    'border-style: inset; }\n'
)


def back_link(href="../index.html", label="← Volver al índice"):
    """Back-link al índice del sitio (primera pieza del <body>/header).

    La ruta relativa es ../index.html porque las páginas publicadas viven
    en data/ y el índice en la raíz de Pages; el guard en
    tests/test_site_nav.py falla si alguna página generada lo pierde.
    """
    return f'<a class="back-index" href="{href}">{label}</a>'


def css_vars():
    """Bloque :root con exactamente las variables de TOKENS (orden estable)."""
    body = "".join(f"{k}:{v};" for k, v in TOKENS.items())
    return ":root {" + body + "}"


def base_css():
    """Reglas base compartidas: box-sizing, tipografía y marginalia.

    Cada generador añade sus reglas específicas DESPUÉS de esta base;
    ninguna define su propia paleta ni font-family alternativa.
    """
    return (
        css_vars() + "\n"
        "* { box-sizing: border-box; }\n"
        "body { font-family: var(--font-stack); }\n"
        + MARGINALIA_CSS
        + BACK_LINK_CSS
    )
