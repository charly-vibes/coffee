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
import viz_fpa_render_sections  # noqa: F402,E402
import viz_fpa_render_script  # noqa: F402,E402



def _partial_banner(model):
    """Banner de meses parciales: comparaciones a tasa diaria."""
    months_meta = model["months"]
    partials = [m for m in months_meta if m["partial"]]
    banner = ""
    if partials:
        bits = [f'{m["label"]} <strong>{m["elapsed"]}/{m["total_days"]}</strong>'
                for m in partials]
        banner = ('<p class="banner-partial">Mes parcial: '
                  + ", ".join(bits)
                  + ' — las comparaciones del mes parcial usan tasas diarias.</p>')
    return banner


def _tracker_and_period_cells(report, model):
    """Celdas de metadata del header: versión del tracker y periodo."""
    tracker_version = report["metadata"].get("tracker_version")
    tracker_cell = (f"Tracker {tracker_version}" if tracker_version
                    else "Tracker n/a (versión no registrada en el reporte)")
    period = model["period"]
    period_cell = " → ".join(p or "n/a" for p in (period["start"], period["end"]))
    return tracker_cell, period_cell


def _claims_block(model):
    """Claims ejecutivos; envueltos en su div cuando hay alguno."""
    claims_html = "".join(claim_html(c) for c in model["claims"])
    if claims_html:
        claims_html = f"<div class='claims'>{claims_html}</div>"
    return claims_html


def _kpi_and_trees(model):
    """KPI strip y árboles de la vista \"all\" (coffe-esc F3)."""
    all_view = model["views"]["all"]
    # coffe-esc F3: la interp de cada headline pasa al KPI card de la vista "all" (por key).
    HEADLINE_TO_KPI = {"cost_effective": "cost_effective",
                       "cost_cash": "cost_cash",
                       "leverage": "leverage",
                       "cost_per_1k": "cost_per_1k_eff",
                       "outcome": "cost_per_commit"}
    interp_by_key = {HEADLINE_TO_KPI[h["key"]]: h["interp"]
                     for h in model["headlines"] if h["interp"]}
    for k in all_view["kpis"]:
        if k["key"] in interp_by_key:
            k["headline_interp"] = interp_by_key[k["key"]]
    kpis_html = "".join(kpi_card(k) for k in all_view["kpis"])
    trees_html = (
        tree_section("time", "Árbol Time (Año → Trimestre → Mes)",
                     all_view["trees"]["time"], TreeSectionOpts(is_time=True))
        + tree_section("tool", "Árbol Tool (Tool → Model)",
                       all_view["trees"]["tool"])
        + tree_section("portfolio", "Árbol Portfolio (Categoría → Proyecto)",
                       all_view["trees"]["portfolio"], TreeSectionOpts(open_=True)))
    return kpis_html, trees_html


def _view_blocks(model, report, cfg):
    """Pre-render de los bloques de sección (F2-F8, CTAs)."""
    return {
        "f4": (alerts_html(model["alerts"])          # F4: alertas + economía
               + plan_economy_html(model["plan_economy"])),
        # coffe-a31.3 (CRG-F2): cash real del ledger y reconciliación FPA-082
        "recon": reconciliation_html(model["reconciliation"]),
        # F5: patrones de uso; pareto/timeline separados del bloque (FPA-157/158)
        "f5": usage_html(model["usage"]),
        "pareto": pareto_html(model["usage"]),
        "timeline": timeline_html(model["usage"]),
        "notes": data_notes_html(model["data_notes"]),
        "budget_bridge": (budget_html(model["budget"])   # F3 en Costo
                          + bridge_html(model["bridge"])),
        "forecast": forecast_html(model["forecast"]),    # forecast en Outlook
        # coffe-dnx.6: vista Modelos — scorecard de cohorte (coffe-dnx.8)
        "models": models_html(report, cfg),
        "energy": energy_html(model["energy"]),          # coffe-5ng (F8)
        "gasto": daily_spend_html(model["daily_spend"]),  # add-session-spend
        "ctas": ctas_html(cfg),                          # F6: CTAs (FPA-165/168)
    }


def _tab_and_period_html(views):
    """Tabs de vistas y opciones del selector de periodo."""
    tabs = ("summary", "cost", "breakdown", "models", "gasto", "habits",
            "outlook", "energy", "data")
    tab_labels = {"summary": "Resumen", "cost": "Costo",
                  "breakdown": "Desglose", "models": "Modelos",
                  "gasto": "Gasto diario",
                  "habits": "Hábitos", "outlook": "Pronóstico",
                  "energy": "Energía", "data": "Datos y método"}
    tab_html = "".join(
        f'<a class="tab" role="tab" href="#{k}" data-view="{k}"'
        f'{" aria-current=\"true\"" if k == "summary" else ""}>'
        f'{tab_labels[k]}</a>' for k in tabs)
    # Selector de periodo: presets + rangos custom, todos pre-calculados
    preset_opts = "".join(
        f'<option value="{esc_html(k)}">{esc_html(v["label"])}</option>'
        for k, v in views.items() if not k.startswith("range:"))
    range_opts = "".join(
        f'<option value="{esc_html(k)}">{esc_html(v["label"])}</option>'
        for k, v in views.items() if k.startswith("range:"))
    period_options = (
        f'<optgroup label="Presets">{preset_opts}</optgroup>'
        f'<optgroup label="Rango custom">{range_opts}</optgroup>')
    return tab_html, period_options


def _marginalia(report, cfg):
    """Marginalia progressive disclosure (coffe-gen.4): strip por superficie
    con chips de los análisis mapeados (scripts/viz_fpa_guide.py); los tokens
    {{fig:...}} se resuelven contra el reporte vigente (coffe-udt/0zp)."""
    if guide.GUIDE_MD.exists():
        guide_dict = guide.load_guide(guide.resolve_figures(
            guide.GUIDE_MD.read_text(encoding="utf-8"), report, cfg))
        # coffe-8kx: figures para los chips (resuelve {{fig:*}} de energía;
        # sin esto el chip citaría 0.0 kWh en el fallback sin config, FPA-008)
        figures = guide.derive_figures(report, cfg)
        return {
            s: guide.marginalia_html(guide_dict, cfg, s, figures=figures)
            for s in ("summary", "cost", "breakdown", "models", "gasto",
                      "habits", "outlook", "energy", "data")}
    return {s: "" for s in ("summary", "cost", "breakdown", "models",
                            "gasto", "habits", "outlook", "energy",
                            "data")}


def _body_inner(model, report, cfg):
    """Cuerpo <main> (F2-F8) + numeración/export/tipografía (FPA-144, coffe-6lz)."""
    banner = _partial_banner(model)
    claims_html = _claims_block(model)
    kpis_html, trees_html = _kpi_and_trees(model)
    blocks = _view_blocks(model, report, cfg)
    marginalia = _marginalia(report, cfg)
    body = f"""<main id="main">
  {banner}
  <section id="summary" class="fpa-view" aria-label="Resumen ejecutivo">
    <h2>¿Cuál es el estado de las cosas?</h2>
    {marginalia["summary"]}
    {claims_html}
    <h2 class="sub">KPIs del periodo</h2>
    <div class="cards-grid" id="kpi-cards">{kpis_html}</div>
    <p class="small" id="view-limitation" hidden></p>
    {blocks['ctas']}
  </section>
  <section id="cost" class="fpa-view" aria-label="Costo">
    <h2>¿Qué estoy gastando?</h2>
    {marginalia["cost"]}
    {blocks['budget_bridge']}
    {blocks['f4']}
    {blocks['recon']}
  </section>
  <section id="gasto" class="fpa-view" aria-label="Gasto diario">
    <h2>¿Cuánto gasté hoy y en qué?</h2>
    {marginalia["gasto"]}
    {blocks['gasto']}
  </section>
  <section id="breakdown" class="fpa-view" aria-label="Desglose">
    <h2>¿A dónde va el gasto?</h2>
    {marginalia["breakdown"]}
    <div id="tree-box">{trees_html}</div>
    {blocks['pareto']}
  </section>
  <section id="models" class="fpa-view" aria-label="Modelos">
    <h2>¿Qué modelo me conviene?</h2>
    {marginalia["models"]}
    {blocks['models']}
  </section>
  <section id="habits" class="fpa-view" aria-label="Hábitos">
    <h2>¿Cómo trabajo?</h2>
    {marginalia["habits"]}
    {blocks['f5']}
  </section>
  <section id="outlook" class="fpa-view" aria-label="Pronóstico">
    <h2>¿Qué viene después?</h2>
    {marginalia["outlook"]}
    {blocks['forecast']}
  </section>
  <section id="energy" class="fpa-view" aria-label="Energía">
    <h2>¿Cuánta energía consume?</h2>
    {marginalia["energy"]}
    {blocks['energy']}
  </section>
  <section id="data" class="fpa-view" aria-label="Datos y método">
    <h2>Datos y método</h2>
    {marginalia["data"]}
    <details class="tree" data-tree="data-method" id="data-method" open>
      <summary><h3>Datos y método</h3></summary>
      {blocks['notes']}
      {blocks['timeline']}
      <p class="small">Método de render: HTML único generado por
      viz-fpa.py (Python stdlib-only, SVG inline); las matemáticas se
      calculan en Python y el JS solo re-escala (FPA-145).</p>
      <p class="small">¿Qué significa cada análisis? La
      <a href="fpa-guide.html">guía de análisis a 4 niveles</a> explica
      cada uno desde el nivel más simple hasta el más técnico (fuente:
      docs/fpa-analyses-guide.md).</p>
      <p class="small">Descargas: botones Exportar CSV (tablas) y
      Descargar SVG (charts) en cada figura, sin librerías externas.</p>
    </details>
  </section>
</main>"""

    # FPA-144: numeración de tablas/figuras, luego botones de export (nunca sobre el JS)
    body = number_figures(body)
    body = add_export_buttons(body)
    # coffe-6lz F4: los ids FPA-xxx salen del cuerpo visible y viajan al title del contenedor.
    return move_fpa_ids_to_titles(body)


def _page_shell(model, report, cfg, generated):
    """Cascara de la página: head, header, topbar, footer y JS embebido."""
    lang = cfg.get("language", "es")
    site_name = cfg.get("site_name", "Uso y costos de IA")
    retro = cfg.get("retro", {}).get("enabled", False)
    tracker_cell, period_cell = _tracker_and_period_cells(report, model)
    tab_html, period_options = _tab_and_period_html(model["views"])
    body_inner = _body_inner(model, report, cfg)
    model_json = json.dumps(model, ensure_ascii=False, sort_keys=True)
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Ctext y='13' font-size='13'%3E%E2%98%95%3C/text%3E%3C/svg%3E">
<title>{site_name}</title>
<style>{CSS}{CSS_LEGACY if retro else ""}</style>
</head>
<body data-view="summary">
<a class="skip" href="#summary">Saltar al contenido</a>
<header class="site">
  {site_theme.back_link()}
  <h1>{site_name}</h1>
  <p class="meta">Generado: {generated} · {tracker_cell} · periodo
  {period_cell}</p>
  {'<p class="retro">desk de métricas · edición quarterly</p>' if retro else ""}
</header>
<nav class="topbar" aria-label="Vistas y periodo">
  <div class="tabs" role="tablist">{tab_html}</div>
  <select id="period-select" aria-label="Seleccionar periodo">
    {period_options}
  </select>
  <button id="share-view" type="button">Compartir vista</button>
  <span id="wf-readout" aria-live="polite"></span>
</nav>
{body_inner}
<footer class="site">
  <p class="retro small">{site_name} · generado por viz-fpa.py (stdlib-only,
  SVG inline) · cifras con tag reported/assumed</p>
</footer>
<script type="application/json" id="fpa-model">{model_json}</script>
<script>
{viz_fpa_render_sections._PAGE_SCRIPT_UI}{viz_fpa_render_script._PAGE_SCRIPT_VIEWS}</script>
</body>
</html>"""



def render_html(report, cfg, generated=None, today=None):
    """Generar el HTML completo (determinista salvo `generated`, FPA-104)."""
    generated = generated or datetime.now().strftime("%Y-%m-%d %H:%M")
    model = build_model(report, cfg)
    # F4: alertas y economía fuera del modelo base (staleness depende de today; FPA-104/105).
    model["alerts"] = build_alerts(report, cfg, today=today)
    model["plan_economy"] = build_plan_economy(report, cfg)
    return _page_shell(model, report, cfg, generated)
from viz_fpa_common import _link  # noqa: E402

_link(globals())
